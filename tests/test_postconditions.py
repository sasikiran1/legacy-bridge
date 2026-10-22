from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from testbed.postconditions import Snapshot, check, diff, normalize
from testbed.tasks import NOT_NULL, Expectation, RowInsert, RowUpdate


def base() -> Snapshot:
    return {
        "member": {
            1: {"id": 1, "member_number": "M1000001", "city": "Albany", "version": 0, "updated_at": None},
            2: {"id": 2, "member_number": "M1000002", "city": "Utica", "version": 0, "updated_at": None},
        },
        "member_note": {
            7: {"id": 7, "member_id": 1, "body": "Called member.", "deleted_at": None, "deleted_by": None},
        },
        "reimbursement": {},
    }


def test_identical_snapshots_have_no_diff() -> None:
    assert diff(base(), base()).empty


def test_expected_update_passes_and_ignores_audit_columns() -> None:
    after = copy.deepcopy(base())
    after["member"][1].update(city="Troy", version=1, updated_at=dt.datetime(2026, 10, 21, tzinfo=dt.UTC))
    expectation = Expectation(
        changes=(RowUpdate(table="member", match={"member_number": "M1000001"}, set={"city": "Troy"}),)
    )
    result = check(expectation, base(), after)
    assert result.passed, result


def test_change_to_another_row_is_a_wrong_action() -> None:
    after = copy.deepcopy(base())
    after["member"][1]["city"] = "Troy"
    after["member"][2]["city"] = "Rome"
    expectation = Expectation(
        changes=(RowUpdate(table="member", match={"member_number": "M1000001"}, set={"city": "Troy"}),)
    )
    result = check(expectation, base(), after)
    assert not result.passed
    assert result.wrong_action
    assert result.unexpected == ("member#2 changed ['city']",)


def test_missing_change_fails_without_wrong_action() -> None:
    expectation = Expectation(
        changes=(RowUpdate(table="member", match={"member_number": "M1000001"}, set={"city": "Troy"}),)
    )
    result = check(expectation, base(), base())
    assert not result.passed
    assert not result.wrong_action


def test_soft_delete_matches_the_not_null_sentinel() -> None:
    after = copy.deepcopy(base())
    after["member_note"][7].update(deleted_at=dt.datetime(2026, 10, 21, tzinfo=dt.UTC), deleted_by="clerk01")
    expectation = Expectation(
        changes=(RowUpdate(table="member_note", match={"id": "7"}, set={"deleted_at": NOT_NULL}),)
    )
    assert check(expectation, base(), after).passed


def test_insert_matches_on_a_subset_of_columns() -> None:
    after = copy.deepcopy(base())
    after["reimbursement"][1] = {
        "id": 1,
        "reference": "RB-123456",
        "member_id": 1,
        "amount": Decimal("12.30"),
    }
    expectation = Expectation(
        changes=(RowInsert(table="reimbursement", values={"member_id": "1", "amount": "12.30"}),)
    )
    assert check(expectation, base(), after).passed


def test_unexpected_insert_is_a_wrong_action() -> None:
    after = copy.deepcopy(base())
    after["member_note"][8] = {
        "id": 8,
        "member_id": 1,
        "body": "injected",
        "deleted_at": None,
        "deleted_by": None,
    }
    result = check(Expectation(answer="APPROVED"), base(), after, answer="approved")
    assert result.unexpected == ("member_note#8 inserted",)


def test_read_answers_compare_case_and_whitespace_insensitively() -> None:
    assert check(Expectation(answer="IN_REVIEW"), base(), base(), answer="  in_review.\n").passed
    assert not check(Expectation(answer="IN_REVIEW"), base(), base(), answer="DENIED").passed


@given(city=st.text(min_size=1, max_size=30).filter(lambda s: s.strip() and s.strip() != "Albany"))
def test_any_single_field_change_is_detected_exactly_once(city: str) -> None:
    after = copy.deepcopy(base())
    after["member"][1]["city"] = city
    changes = diff(base(), after)
    assert [(c.table, c.row_id, sorted(c.columns)) for c in changes.updated] == [("member", 1, ["city"])]


@given(amount=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("99999.99"), places=2))
def test_decimal_normalization_round_trips_two_places(amount: Decimal) -> None:
    assert Decimal(normalize(amount)) == amount
