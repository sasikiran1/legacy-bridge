"""Guardrails: which actions need a human, verification walls, and treating page text as untrusted data."""

from __future__ import annotations

import re
from enum import StrEnum
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
            inputs = self._tokenizer.prepare_for_model(chunk, return_tensors="pt", add_special_tokens=True)
            with torch.no_grad():
                logits = self._model(input_ids=inputs["input_ids"].unsqueeze(0)).logits
            best = max(best, float(torch.softmax(logits, dim=-1)[0, -1]))
        return ScanResult(flagged=best >= self._threshold, score=best, scanner=self.name)


def build_scanner(name: str) -> InjectionScanner:
    return PromptGuardScanner() if name == "promptguard" else NoScanner()
