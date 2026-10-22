from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pytest

from testbed.seed import generate
from testbed.tasks import INTENT_COUNTS, Intent, RowInsert, Task, load_tasks, money

TASKS = load_tasks()


def test_suite_has_fifty_tasks_with_the_planned_intent_mix() -> None:
    assert len(TASKS) == 50
    assert Counter(t.intent for t in TASKS) == INTENT_COUNTS
    assert len({t.id for t in TASKS}) == len(TASKS)


def test_each_task_targets_a_distinct_member() -> None:
    assert len({t.member_number for t in TASKS}) == len(TASKS)


def test_all_canary_and_injected_members_are_exercised() -> None:
    ds, _, _ = generate()
    injected = {ds.member(n.member_id).member_number for n in ds.notes if n.injected}
    targeted = {t.member_number for t in TASKS}
    assert set(ds.canary_member_numbers) <= targeted
    assert injected <= targeted


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_instruction_carries_every_parameter(task: Task) -> None:
    for key, value in task.params.items():
        shown = money(Decimal(value)) if key == "amount" else value
        assert shown in task.instruction, key


def test_reads_expect_answers_and_writes_expect_changes() -> None:
    reads = {Intent.CLAIM_STATUS, Intent.POLICY_LOOKUP}
    for task in TASKS:
        if task.intent in reads:
            assert task.expected.answer
            assert not task.expected.changes
        else:
            assert task.expected.changes
            assert task.expected.answer is None


def test_payment_and_delete_tasks_require_approval() -> None:
    for task in TASKS:
        expected = task.intent in {Intent.SUBMIT_REIMBURSEMENT, Intent.DELETE_NOTE}
        assert task.requires_approval is expected
        assert task.destructive is (task.intent is Intent.DELETE_NOTE)


def test_reimbursement_routing_numbers_pass_the_aba_checksum() -> None:
    weights = (3, 7, 1, 3, 7, 1, 3, 7, 1)
    for task in TASKS:
        if task.intent is Intent.SUBMIT_REIMBURSEMENT:
            change = task.expected.changes[0]
            assert isinstance(change, RowInsert)
            digits = [int(d) for d in change.values["bank_routing"]]
            assert sum(d * w for d, w in zip(digits, weights, strict=True)) % 10 == 0
