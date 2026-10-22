"""Append-only, hash-chained audit log of every mask and unmask. Entries carry tokens and types, never values.

    uv run python -m proxy.audit verify <file.jsonl>

Each entry stores the SHA-256 of the previous entry, so editing, deleting or reordering any entry breaks every
hash after it. Truncating the tail is only detectable against a recorded head hash, which is why run manifests
record `head` from `verify`.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import sys
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

GENESIS = "0" * 64


class Action(StrEnum):
    MASK = "mask"
    UNMASK = "unmask"
    UNMASK_DENIED = "unmask_denied"
    UNRESOLVED = "unresolved"


class AuditEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int
    ts: str
    task_id: str
    action: Action
    entity_type: str
    token: str
    destination: str
    prev: str
    hash: str


def _digest(fields: dict[str, object]) -> str:
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._seq, self._head = self._tail()

    def _tail(self) -> tuple[int, str]:
        if not self.path.exists():
            return 0, GENESIS
        last = None
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last = line
        if last is None:
            return 0, GENESIS
        entry = AuditEntry.model_validate_json(last)
        return entry.seq, entry.hash

    @property
    def head(self) -> str:
        return self._head

    async def record(
        self, task_id: str, action: Action, entity_type: str, token: str, destination: str
    ) -> AuditEntry:
        async with self._lock:
            fields: dict[str, object] = {
                "seq": self._seq + 1,
                "ts": dt.datetime.now(dt.UTC).isoformat(timespec="microseconds"),
                "task_id": task_id,
                "action": action.value,
                "entity_type": entity_type,
                "token": token,
                "destination": destination,
                "prev": self._head,
            }
            entry = AuditEntry.model_validate({**fields, "hash": _digest(fields)})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(entry.model_dump_json() + "\n")
            self._seq, self._head = entry.seq, entry.hash
            return entry


class Verification(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    entries: int
    head: str
    error: str | None = None


def verify(path: Path) -> Verification:
    prev, seq, count = GENESIS, 0, 0
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                entry = AuditEntry.model_validate_json(line)
            except ValueError as e:
                return Verification(
                    ok=False, entries=count, head=prev, error=f"line {line_number}: unparseable ({e})"
                )
            fields = entry.model_dump(mode="json", exclude={"hash"})
            if entry.prev != prev:
                return Verification(
                    ok=False, entries=count, head=prev, error=f"line {line_number}: chain broken"
                )
            if entry.seq != seq + 1:
                return Verification(
                    ok=False, entries=count, head=prev, error=f"line {line_number}: sequence gap"
                )
            if _digest(fields) != entry.hash:
                return Verification(
                    ok=False, entries=count, head=prev, error=f"line {line_number}: hash mismatch"
                )
            prev, seq, count = entry.hash, entry.seq, count + 1
    return Verification(ok=True, entries=count, head=prev)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "verify":
        print("usage: python -m proxy.audit verify <file.jsonl>", file=sys.stderr)
        return 2
    result = verify(Path(args[1]))
    print(result.model_dump_json())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
