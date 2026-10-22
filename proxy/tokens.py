"""Typed, deterministic-per-task tokens: `[[TYPE:hhhhhhhhhh]]` (ADR 0001, rendering per ADR 0011)."""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass

SHORT_ID_LENGTH = 10
TOKEN_RE = re.compile(rf"\[\[([A-Z][A-Z_]*):([0-9a-f]{{{SHORT_ID_LENGTH}}})\]\]")
OPEN, CLOSE = "[[", "]]"
MAX_TOKEN_LENGTH = 48
"""Upper bound on a rendered token, used by the streaming lookahead buffer."""


@dataclass(frozen=True)
class Token:
    entity_type: str
    canonical: uuid.UUID

    @property
    def short_id(self) -> str:
        return self.canonical.hex[:SHORT_ID_LENGTH]

    def render(self) -> str:
        return f"{OPEN}{self.entity_type}:{self.short_id}{CLOSE}"


class TokenMinter:
    """Derives tokens with keyed HMACs so equal values map to equal tokens within a task, and only there."""

    def __init__(self, key: bytes, run_seed: str) -> None:
        if len(key) < 16:
            raise ValueError("token key must be at least 16 bytes")
        self._key = key
        self._run_seed = run_seed.encode()

    def _task_key(self, task_id: str) -> bytes:
        message = b"lb-token-v1\x00" + self._run_seed + b"\x00" + task_id.encode()
        return hmac.new(self._key, message, hashlib.sha256).digest()

    def mint(self, task_id: str, entity_type: str, value: str) -> Token:
        digest = hmac.new(
            self._task_key(task_id), entity_type.encode() + b"\x1f" + value.encode(), hashlib.sha256
        ).digest()
        return Token(entity_type, uuid.UUID(bytes=digest[:16], version=8))


def find_tokens(text: str) -> list[re.Match[str]]:
    return list(TOKEN_RE.finditer(text))
