"""Masking: replace detected values with tokens, store the mapping in the vault, audit every replacement."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from collections.abc import Sequence

from proxy.audit import Action, AuditLog
from proxy.detection import NUMERIC_TYPES, Detector, Span, resolve_overlaps
from proxy.policy import PiiPolicy
from proxy.tokens import TOKEN_RE, TokenMinter
from proxy.vault import Vault, VaultEntry

MIN_KNOWN_VALUE_LENGTH = 4
"""Shorter vault values (e.g. a two-letter state) are not re-matched as free text; too many false hits."""

CACHE_SIZE = 4096
"""Masked results kept per (task, text). Executors mask tool results and the proxy masks the same text again
when it reaches an LLM; the cache keeps that second pass from re-running detection."""

MIN_TRUSTED_DIGITS = 6
"""A one-word NER span still propagates if it is a numeric type with this many digits: an account number
detected beside "Bank account number" must also be caught beside the drifted label "Account no."."""


def _inside_word(text: str, start: int, end: int) -> bool:
    """Whether text[start:end] is glued to a letter or digit on a side where the value is alphanumeric."""
    before = start > 0 and text[start - 1].isalnum() and text[start].isalnum()
    after = end < len(text) and text[end].isalnum() and text[end - 1].isalnum()
    return before or after


def trusted(span: Span, value: str) -> bool:
    """Whether a masked value is re-matched wherever it reappears in the task: structured fields and pattern
    rules, multi-word NER spans, and long numbers. Single words from NER are not, because a mis-tagged word
    ("Member") would then be masked on every page."""
    if not span.source.startswith("presidio") or len(value.split()) > 1:
        return True
    return span.entity_type in NUMERIC_TYPES and sum(ch.isdigit() for ch in value) >= MIN_TRUSTED_DIGITS


JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class Masker:
    def __init__(
        self,
        detector: Detector,
        minter: TokenMinter,
        vault: Vault,
        audit: AuditLog,
        policy: PiiPolicy,
        light_detector: Detector | None = None,
    ) -> None:
        """`light_detector` (pattern rules only) handles model-authored text: the model only ever saw tokens,
        so raw PII in its output must be reconstructed, and precise detectors plus known values catch that
        without NER false positives on its short tool arguments."""
        self._detector = detector
        self._light = light_detector or detector
        self._minter = minter
        self._vault = vault
        self._audit = audit
        self._policy = policy
        self._cache: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._propagate: dict[str, set[str]] = {}

    async def mask_text(self, text: str, task_id: str, destination: str, model_authored: bool = False) -> str:
        if not text:
            return text
        key = (task_id, hashlib.sha256((("m:" if model_authored else "") + text).encode()).hexdigest())
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        spans = await self._spans(text, task_id, model_authored)
        masked = await self._replace(text, spans, task_id, destination)
        self._cache[key] = masked
        if len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return masked

    def forget(self, task_id: str) -> None:
        for key in [k for k in self._cache if k[0] == task_id]:
            del self._cache[key]
        self._propagate.pop(task_id, None)

    async def _spans(self, text: str, task_id: str, model_authored: bool) -> list[Span]:
        detector = self._light if model_authored else self._detector
        detected = await asyncio.to_thread(detector.detect, text)
        candidates = [
            s
            for s in detected
            if self._policy.tokenizes(s.entity_type) and s.score >= self._policy.min_score(s.entity_type)
        ]
        candidates += await self._known_values(text, task_id)
        protected = [(m.start(), m.end()) for m in TOKEN_RE.finditer(text)]
        return [
            s for s in resolve_overlaps(candidates) if all(s.end <= a or s.start >= b for a, b in protected)
        ]

    async def tokenize_spans(self, text: str, task_id: str, destination: str) -> list[tuple[int, int, str]]:
        """(start, end, token) for every value masking would replace in `text`: for OCR text, where the
        caller redacts pixels rather than characters. Vaulted and audited like any other masking."""
        spans = await self._spans(text, task_id, model_authored=False)
        located = []
        for span in sorted(spans, key=lambda s: s.start):
            value = text[span.start : span.end]
            token = await self._token_for(value, span.entity_type, task_id, destination, trusted(span, value))
            located.append((span.start, span.end, token))
        return located

    async def mask_value(
        self,
        value: JsonValue,
        task_id: str,
        destination: str,
        key: str | None = None,
        model_authored: bool = False,
    ) -> JsonValue:
        """Masks a JSON value. Values under keys the policy recognises are tokenized whole, without NER."""
        match value:
            case str():
                field_type = self._policy.field_type(key) if key else None
                if field_type and value and not TOKEN_RE.fullmatch(value):
                    return await self._token_for(value, field_type, task_id, destination, propagate=True)
                return await self.mask_text(value, task_id, destination, model_authored)
            case list():
                return [
                    await self.mask_value(item, task_id, destination, key, model_authored) for item in value
                ]
            case dict():
                return {
                    k: await self.mask_value(v, task_id, destination, k, model_authored)
                    for k, v in value.items()
                }
        return value

    async def mask_content(
        self, content: str, task_id: str, destination: str, model_authored: bool = False
    ) -> str:
        """Message content: JSON documents (tool results, API responses) get field-aware masking."""
        stripped = content.lstrip()
        if stripped[:1] in "{[":
            try:
                parsed = json.loads(content)
            except ValueError:
                pass
            else:
                masked = await self.mask_value(parsed, task_id, destination, model_authored=model_authored)
                return json.dumps(masked, ensure_ascii=False)
        return await self.mask_text(content, task_id, destination, model_authored)

    async def _known_values(self, text: str, task_id: str) -> list[Span]:
        """Values already tokenized in this task get the same token wherever they reappear.

        Only trusted values are propagated (see `trusted`). A single-word NER guess ("Member" read as a
        person) is masked where it was found but not spread, or it would re-tokenize that word on every later
        page.
        """
        spans = []
        propagated = self._propagate.get(task_id, set())
        for token, entry in (await self._vault.entries(task_id)).items():
            if len(entry.value) < MIN_KNOWN_VALUE_LENGTH or token not in propagated:
                continue
            start = text.find(entry.value)
            while start != -1:
                end = start + len(entry.value)
                if _inside_word(text, start, end):
                    # "eric" inside "generic" is not the value; masking it broke snapshots ("ge[[PERSON]]").
                    start = text.find(entry.value, start + 1)
                    continue
                spans.append(
                    Span(start=start, end=end, entity_type=entry.entity_type, score=1.0, source="known")
                )
                start = text.find(entry.value, start + 1)
        return spans

    async def _token_for(
        self, value: str, entity_type: str, task_id: str, destination: str, propagate: bool
    ) -> str:
        token = self._minter.mint(task_id, entity_type, value).render()
        await self._vault.put(task_id, token, VaultEntry(entity_type=entity_type, value=value))
        if propagate:
            self._propagate.setdefault(task_id, set()).add(token)
        await self._audit.record(task_id, Action.MASK, entity_type, token, destination)
        return token

    async def _replace(self, text: str, spans: Sequence[Span], task_id: str, destination: str) -> str:
        pieces, cursor = [], 0
        for span in sorted(spans, key=lambda s: s.start):
            pieces.append(text[cursor : span.start])
            value = text[span.start : span.end]
            pieces.append(
                await self._token_for(value, span.entity_type, task_id, destination, trusted(span, value))
            )
            cursor = span.end
        pieces.append(text[cursor:])
        return "".join(pieces)
