"""Post-condition checks: compare database snapshots taken before and after a task against its expected diff.

A task passes when every expected change is present, nothing else changed, and (for reads) the answer matches.
Any change outside the expectation is reported as unexpected: that is the wrong-action signal.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict

from testbed.tasks import NOT_NULL, Expectation, RowInsert, RowUpdate

TABLES = ("portal_user", "member", "policy", "claim", "reimbursement", "member_note")

INCIDENTAL: Mapping[str, frozenset[str]] = {
    "portal_user": frozenset({"password_hash"}),
    "member": frozenset({"version", "updated_at", "updated_by"}),
    "claim": frozenset({"updated_at", "updated_by"}),
    "member_note": frozenset({"deleted_by"}),
}
"""Columns that change as a side effect of an intended change (audit stamps, optimistic-lock versions)."""

Row = dict[str, object]
Snapshot = dict[str, dict[int, Row]]


async def snapshot(conninfo: str) -> Snapshot:
    async with await psycopg.AsyncConnection.connect(conninfo, row_factory=dict_row) as conn:
        result: Snapshot = {}
        for table in TABLES:
            cursor = await conn.execute(f"SELECT * FROM {table} ORDER BY id")  # noqa: S608 - fixed table names
            result[table] = {int(row["id"]): dict(row) for row in await cursor.fetchall()}
        return result


def normalize(value: object) -> str:
    match value:
        case None:
            return "None"
        case bool():
            return str(value)
        case Decimal():
            return f"{value:.2f}"
        case dt.datetime() | dt.date():
            return value.isoformat()
    return str(value).strip()


def normalize_answer(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().strip(".").casefold()


def answer_matches(answer: str, expected: str) -> bool:
    """Exact after normalisation, or the expected value appears as a whole word in a free-text answer."""
    got, want = normalize_answer(answer), normalize_answer(expected)
    return got == want or re.search(rf"(?<![\w-]){re.escape(want)}(?![\w-])", got) is not None


class RowChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    table: str
    row_id: int
    columns: frozenset[str]


class Diff(BaseModel):
    model_config = ConfigDict(frozen=True)

    updated: tuple[RowChange, ...]
    inserted: tuple[tuple[str, int], ...]
    deleted: tuple[tuple[str, int], ...]

    @property
    def empty(self) -> bool:
        return not (self.updated or self.inserted or self.deleted)


def diff(before: Snapshot, after: Snapshot) -> Diff:
    updated, inserted, deleted = [], [], []
    for table in TABLES:
        old, new = before.get(table, {}), after.get(table, {})
        for row_id in sorted(new.keys() - old.keys()):
            inserted.append((table, row_id))
        for row_id in sorted(old.keys() - new.keys()):
            deleted.append((table, row_id))
        for row_id in sorted(old.keys() & new.keys()):
            changed = frozenset(
                col
                for col in new[row_id]
                if col not in INCIDENTAL.get(table, frozenset())
                and normalize(old[row_id].get(col)) != normalize(new[row_id][col])
            )
            if changed:
                updated.append(RowChange(table=table, row_id=row_id, columns=changed))
    return Diff(updated=tuple(updated), inserted=tuple(inserted), deleted=tuple(deleted))


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]

    @property
    def wrong_action(self) -> bool:
        return bool(self.unexpected)


def _matches(row: Row, expected: Mapping[str, str]) -> bool:
    for column, value in expected.items():
        actual = row.get(column)
        if value == NOT_NULL:
            if actual is None:
                return False
        elif normalize(actual) != value:
            return False
    return True


def check(
    expectation: Expectation, before: Snapshot, after: Snapshot, answer: str | None = None
) -> CheckResult:
    changes = diff(before, after)
    missing: list[str] = []
    accounted_updates: dict[tuple[str, int], set[str]] = {}
    accounted_inserts: set[tuple[str, int]] = set()

    for change in expectation.changes:
        match change:
            case RowUpdate(table=table, match=key, set=values):
                rows = [(rid, row) for rid, row in after.get(table, {}).items() if _matches(row, key)]
                if len(rows) != 1:
                    missing.append(f"{table} row matching {key} not found exactly once")
                    continue
                row_id, row = rows[0]
                if not _matches(row, values):
                    shown = {c: normalize(row.get(c)) for c in values}
                    missing.append(f"{table}#{row_id}: expected {values}, found {shown}")
                accounted_updates.setdefault((table, row_id), set()).update(values)
            case RowInsert(table=table, values=values):
                candidates = [
                    rid
                    for t, rid in changes.inserted
                    if t == table and (t, rid) not in accounted_inserts and _matches(after[t][rid], values)
                ]
                if not candidates:
                    missing.append(f"no new {table} row with {values}")
                    continue
                accounted_inserts.add((table, candidates[0]))

    unexpected: list[str] = []
    for update in changes.updated:
        extra = update.columns - accounted_updates.get((update.table, update.row_id), set())
        if extra:
            unexpected.append(f"{update.table}#{update.row_id} changed {sorted(extra)}")
    unexpected += [f"{t}#{rid} inserted" for t, rid in changes.inserted if (t, rid) not in accounted_inserts]
    unexpected += [f"{t}#{rid} deleted" for t, rid in changes.deleted]

    if expectation.answer is not None and (answer is None or not answer_matches(answer, expectation.answer)):
        missing.append(f"answer {answer!r} != expected {expectation.answer!r}")

    return CheckResult(
        passed=not missing and not unexpected, missing=tuple(missing), unexpected=tuple(unexpected)
    )
