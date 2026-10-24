"""Guardrails: which actions need a human, verification walls, and treating page text as untrusted data."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class ApprovalKind(StrEnum):
    PAYMENT = "payment"
    DELETE = "delete"
    WRITE_AFTER_INJECTION = "write_after_injection"


APPROVAL_OPERATIONS = {"submitReimbursement": ApprovalKind.PAYMENT, "deleteNote": ApprovalKind.DELETE}
WRITE_OPERATIONS_WORDS = re.compile(
    r"\b(save|update|add|submit|confirm|request|delete|remove|file|send|appeal|pay)\b", re.I
)
_PAYMENT_CLICK = re.compile(r"\b(confirm submission|submit payment|pay now|send payment)\b", re.I)
_DELETE_CLICK = re.compile(r"\b(delete|remove|confirm removal)\b", re.I)

_WALL = re.compile(
    r'(heading "[^"]*(security check|verify (that )?you|captcha|two-step|two-factor|multi-factor)[^"]*"'
    r'|textbox "[^"]*(verification code|characters)[^"]*")',
    re.I,
)


def approval_for_operation(operation_id: str, quarantined: bool) -> ApprovalKind | None:
    kind = APPROVAL_OPERATIONS.get(operation_id)
    if kind is None and quarantined and operation_id[:3] not in ("get", "lis", "sea"):
        return ApprovalKind.WRITE_AFTER_INJECTION
    return kind


def approval_for_click(role: str, name: str, quarantined: bool) -> ApprovalKind | None:
    if role not in ("button", "link", "menuitem"):
        return None
    if _PAYMENT_CLICK.search(name):
        return ApprovalKind.PAYMENT
    if _DELETE_CLICK.search(name):
        return ApprovalKind.DELETE
    if quarantined and WRITE_OPERATIONS_WORDS.search(name):
        return ApprovalKind.WRITE_AFTER_INJECTION
    return None


def is_verification_wall(snapshot: str) -> bool:
    """CAPTCHA/MFA pages. The agent never attempts them; it hands off to a human."""
    return bool(_WALL.search(snapshot))


CONFIRMATION = re.compile(r"\b(saved|submitted|added|deleted|requested)\b", re.I)
"""The portal's confirmation wording after a write; a browser write is verified only if the page shows it."""


def spotlight(text: str, source: str) -> str:
    """Delimits untrusted content so the system prompt can tell the model it is data, never instructions."""
    return f'<untrusted_content source="{source}">\n{text}\n</untrusted_content>'


class ScanResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    flagged: bool
    score: float
    scanner: str


class InjectionScanner(Protocol):
    name: str

    def scan(self, text: str) -> ScanResult: ...


class NoScanner:
    """Structural defenses only (spotlighting, action allowlists, approvals, post-conditions)."""

    name = "none"

    def scan(self, text: str) -> ScanResult:
        return ScanResult(flagged=False, score=0.0, scanner=self.name)


class PromptGuardScanner:
    """Llama Prompt Guard 2 (ADR 0009), run locally over 512-token chunks with 64-token overlap.

    Gated on Hugging Face: needs HF_TOKEN for an account that has accepted the Llama 4 license.
    """

    def __init__(self, model: str = "meta-llama/Llama-Prompt-Guard-2-86M", threshold: float = 0.5) -> None:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.name = model.rsplit("/", 1)[-1]
        self._tokenizer = AutoTokenizer.from_pretrained(model)
        self._model = AutoModelForSequenceClassification.from_pretrained(model)
        self._threshold = threshold

    def scan(self, text: str) -> ScanResult:
        import torch

        ids = self._tokenizer(text, add_special_tokens=False)["input_ids"]
        chunks = [ids[i : i + 510] for i in range(0, max(1, len(ids)), 446)] or [[]]
        best = 0.0
        for chunk in chunks:
            input_ids = torch.tensor([[self._tokenizer.cls_token_id, *chunk, self._tokenizer.sep_token_id]])
            with torch.no_grad():
                logits = self._model(input_ids=input_ids).logits
            best = max(best, float(torch.softmax(logits, dim=-1)[0, -1]))
        return ScanResult(flagged=best >= self._threshold, score=best, scanner=self.name)


class RecordingScanner:
    """Saves each verdict with the task's cassettes, so a replay can reproduce them without the model."""

    def __init__(self, inner: InjectionScanner, path: Path) -> None:
        self.name = inner.name
        self._inner = inner
        self._path = path
        self._verdicts: list[dict[str, object]] = []

    def scan(self, text: str) -> ScanResult:
        result = self._inner.scan(text)
        self._verdicts.append({"flagged": result.flagged, "score": round(result.score, 4)})
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._verdicts, indent=1) + "\n", encoding="utf-8")
        return result


class ReplayScanner:
    """Serves recorded verdicts in order. A task recorded without them (no scanner, or nothing flagged before
    verdicts were recorded) replays as never flagged."""

    def __init__(self, path: Path, name: str) -> None:
        self.name = name
        self._verdicts = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        self._next = 0

    def scan(self, text: str) -> ScanResult:
        verdict = (
            self._verdicts[self._next]
            if self._next < len(self._verdicts)
            else {"flagged": False, "score": 0.0}
        )
        self._next += 1
        return ScanResult(flagged=bool(verdict["flagged"]), score=float(verdict["score"]), scanner=self.name)


def build_scanner(name: str) -> InjectionScanner:
    return PromptGuardScanner() if name == "promptguard" else NoScanner()
