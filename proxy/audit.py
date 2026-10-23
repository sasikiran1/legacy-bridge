"""Append-only, hash-chained audit log of every mask and unmask. Entries carry tokens and types, never values.

    uv run python -m proxy.audit verify <file.jsonl>
    uv run python -m proxy.audit recover <file.jsonl>

Each entry stores the SHA-256 of the previous entry, so editing, deleting or reordering any entry breaks every
hash after it. Truncating the tail is only detectable against a recorded head hash, which is why run manifests
record `head` from `verify`.

Every entry is fsynced before `record` returns, so nothing is unmasked before its entry is on disk. A crash
can still tear the write in progress (seen as trailing NUL bytes). The log then refuses to open, and `recover`
is the deliberate, manual repair: it accepts only a torn tail before a fully verifying chain, keeps the
original file, and reports what was dropped.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
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


class AuditLogDamaged(Exception):
    pass


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
        try:
            entry = AuditEntry.model_validate_json(last)
        except ValueError as e:
            raise AuditLogDamaged(
                f"{self.path}: the last entry is unreadable, most likely a write torn by a crash. "
                f"Inspect it, then run `python -m proxy.audit recover {self.path}`."
            ) from e
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
                handle.flush()
                os.fsync(handle.fileno())
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


class Recovery(BaseModel):
    model_config = ConfigDict(frozen=True)

    recovered: bool
    entries: int
    head: str
    dropped_bytes: int
    original: str | None
    error: str | None = None


def recover(path: Path) -> Recovery:
    """Repairs a torn tail only: trailing NUL bytes and at most one partial last line, before a chain that
    verifies in full. The damaged file is kept beside the repaired one."""
    data = path.read_bytes()
    kept = data.rstrip(b"\x00")
    if not kept.endswith(b"\n"):
        kept = kept[: kept.rfind(b"\n") + 1]
    if b"\x00" in kept:
        return Recovery(
            recovered=False,
            entries=0,
            head=GENESIS,
            dropped_bytes=0,
            original=None,
            error="NUL bytes before the tail: not a torn write",
        )
    candidate = path.with_name(path.name + ".candidate")
    candidate.write_bytes(kept)
    result = verify(candidate)
    if not result.ok:
        candidate.unlink()
        return Recovery(
            recovered=False,
            entries=result.entries,
            head=result.head,
            dropped_bytes=0,
            original=None,
            error=f"the chain before the tail does not verify: {result.error}",
        )
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    original = path.with_name(f"{path.name}.torn-{stamp}")
    path.rename(original)
    candidate.rename(path)
    return Recovery(
        recovered=True,
        entries=result.entries,
        head=result.head,
        dropped_bytes=len(data) - len(kept),
        original=str(original),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in ("verify", "recover"):
        print("usage: python -m proxy.audit verify|recover <file.jsonl>", file=sys.stderr)
        return 2
    result: Verification | Recovery
    if args[0] == "verify":
        result = verify(Path(args[1]))
        print(result.model_dump_json())
        return 0 if result.ok else 1
    result = recover(Path(args[1]))
    print(result.model_dump_json())
    return 0 if result.recovered else 1


if __name__ == "__main__":
    sys.exit(main())
