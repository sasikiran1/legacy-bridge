"""Masking: replace detected values with tokens, store the mapping in the vault, audit every replacement."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

from proxy.audit import Action, AuditLog
from proxy.detection import Detector, Span, resolve_overlaps
from proxy.policy import PiiPolicy
from proxy.tokens import TOKEN_RE, TokenMinter
from proxy.vault import Vault, VaultEntry

MIN_KNOWN_VALUE_LENGTH = 4
"""Shorter vault values (e.g. a two-letter state) are not re-matched as free text; too many false hits."""

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class Masker:
    def __init__(
        self, detector: Detector, minter: TokenMinter, vault: Vault, audit: AuditLog, policy: PiiPolicy
    ) -> None:
        self._detector = detector
        self._minter = minter
        self._vault = vault
        self._audit = audit
        self._policy = policy

    async def mask_text(self, text: str, task_id: str, destination: str) -> str:
        if not text:
            return text
        detected = await asyncio.to_thread(self._detector.detect, text)
        candidates = [
            s
            for s in detected
            if self._policy.tokenizes(s.entity_type) and s.score >= self._policy.min_score(s.entity_type)
        ]
        candidates += await self._known_values(text, task_id)
        protected = [(m.start(), m.end()) for m in TOKEN_RE.finditer(text)]
        spans = [
            s for s in resolve_overlaps(candidates) if all(s.end <= a or s.start >= b for a, b in protected)
        ]
        return await self._replace(text, spans, task_id, destination)

    async def mask_value(
        self, value: JsonValue, task_id: str, destination: str, key: str | None = None
    ) -> JsonValue:
        """Masks a JSON value. Values under keys the policy recognises are tokenized whole, without NER."""
        match value:
            case str():
                field_type = self._policy.field_type(key) if key else None
                if field_type and value and not TOKEN_RE.fullmatch(value):
                    return await self._token_for(value, field_type, task_id, destination)
                return await self.mask_text(value, task_id, destination)
            case list():
                return [await self.mask_value(item, task_id, destination, key) for item in value]
            case dict():
                return {k: await self.mask_value(v, task_id, destination, k) for k, v in value.items()}
        return value

    async def mask_content(self, content: str, task_id: str, destination: str) -> str:
        """Message content: JSON documents (tool results, API responses) get field-aware masking."""
        stripped = content.lstrip()
        if stripped[:1] in "{[":
            try:
                parsed = json.loads(content)
            except ValueError:
                pass
            else:
                masked = await self.mask_value(parsed, task_id, destination)
                return json.dumps(masked, ensure_ascii=False)
        return await self.mask_text(content, task_id, destination)

    async def _known_values(self, text: str, task_id: str) -> list[Span]:
        """Values already tokenized in this task get the same token wherever they reappear."""
        spans = []
        for entry in (await self._vault.entries(task_id)).values():
            if len(entry.value) < MIN_KNOWN_VALUE_LENGTH:
                continue
            start = text.find(entry.value)
            while start != -1:
                spans.append(
                    Span(
                        start=start,
                        end=start + len(entry.value),
                        entity_type=entry.entity_type,
                        score=1.0,
                        source="known",
                    )
                )
                start = text.find(entry.value, start + 1)
        return spans

    async def _token_for(self, value: str, entity_type: str, task_id: str, destination: str) -> str:
        token = self._minter.mint(task_id, entity_type, value).render()
        await self._vault.put(task_id, token, VaultEntry(entity_type=entity_type, value=value))
        await self._audit.record(task_id, Action.MASK, entity_type, token, destination)
        return token

    async def _replace(self, text: str, spans: Sequence[Span], task_id: str, destination: str) -> str:
        pieces, cursor = [], 0
        for span in sorted(spans, key=lambda s: s.start):
            pieces.append(text[cursor : span.start])
            pieces.append(
                await self._token_for(text[span.start : span.end], span.entity_type, task_id, destination)
            )
            cursor = span.end
        pieces.append(text[cursor:])
        return "".join(pieces)
