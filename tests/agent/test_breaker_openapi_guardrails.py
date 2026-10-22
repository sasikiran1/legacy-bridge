from __future__ import annotations

from typing import Any

from agent.breaker import Breaker, BreakerState
from agent.guardrails import ApprovalKind, approval_for_click, approval_for_operation, is_verification_wall
from agent.openapi import parse


def state(breaker: Breaker) -> BreakerState:
    """Reads the state through a call, so mypy does not carry narrowing across breaker transitions."""
    return breaker.state


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_breaker_opens_after_three_failures_and_half_opens_after_the_interval() -> None:
    clock = Clock()
    breaker = Breaker(clock=clock)
    for _ in range(3):
        assert breaker.allow()
        breaker.record(False)
    assert state(breaker) is BreakerState.OPEN
    assert not breaker.allow()
    clock.now = 31
    assert breaker.allow()
    assert state(breaker) is BreakerState.HALF_OPEN
    breaker.record(True)
    assert state(breaker) is BreakerState.CLOSED


def test_failed_probe_reopens_with_a_doubled_interval() -> None:
    clock = Clock()
    breaker = Breaker(clock=clock)
    for _ in range(3):
        breaker.record(False)
    clock.now = 31
    assert breaker.allow()
    breaker.record(False)
    assert state(breaker) is BreakerState.OPEN
    assert breaker.open_seconds == 60
    clock.now = 31 + 59
    assert not breaker.allow()
    clock.now = 31 + 61
    assert breaker.allow()


def test_breaker_trips_on_failure_ratio_without_consecutive_failures() -> None:
    breaker = Breaker(clock=Clock())
    for outcome in [True, False] * 5:
        breaker.record(outcome)
    assert state(breaker) is BreakerState.OPEN


SPEC: dict[str, Any] = {
    "paths": {
        "/api/v1/members/{memberNumber}": {
            "get": {
                "operationId": "getMember",
                "summary": "Get a member",
                "parameters": [
                    {"name": "memberNumber", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
                "responses": {
                    "200": {"content": {"*/*": {"schema": {"$ref": "#/components/schemas/MemberView"}}}}
                },
            }
        },
        "/api/v1/members/{memberNumber}/address": {
            "put": {
                "operationId": "updateAddress",
                "summary": "Replace the address",
                "parameters": [
                    {"name": "memberNumber", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AddressBody"}}}
                },
                "responses": {
                    "200": {"content": {"*/*": {"schema": {"$ref": "#/components/schemas/MemberView"}}}}
                },
            }
        },
    },
    "components": {
        "schemas": {
            "MemberView": {"properties": {"memberNumber": {}, "firstName": {}, "address": {}}},
            "AddressBody": {"properties": {"street": {}, "city": {}, "state": {}, "postalCode": {}}},
        }
    },
}


def test_operations_become_tools_with_path_and_body_arguments() -> None:
    ops = parse(SPEC)
    update = ops["updateAddress"]
    assert update.writes
    parameters = update.tool()["function"]["parameters"]
    assert set(parameters["properties"]) == {"memberNumber", "street", "city", "state", "postalCode"}
    assert parameters["required"] == ["memberNumber"]
    assert not ops["getMember"].writes


def test_drifted_responses_are_detected_by_missing_documented_fields() -> None:
    get_member = parse(SPEC)["getMember"]
    assert get_member.check_response({"memberNumber": "x", "firstName": "y", "address": {}}) == []
    assert get_member.check_response({"member_no": "x", "name": {}}) == [
        "address",
        "firstName",
        "memberNumber",
    ]


def test_payments_and_deletes_need_approval_and_injection_widens_it_to_writes() -> None:
    assert approval_for_operation("submitReimbursement", quarantined=False) is ApprovalKind.PAYMENT
    assert approval_for_operation("updateAddress", quarantined=False) is None
    assert approval_for_operation("updateAddress", quarantined=True) is ApprovalKind.WRITE_AFTER_INJECTION
    assert approval_for_operation("getMember", quarantined=True) is None
    assert approval_for_click("button", "Yes, delete note", quarantined=False) is ApprovalKind.DELETE
    assert approval_for_click("button", "Confirm submission", quarantined=False) is ApprovalKind.PAYMENT
    assert approval_for_click("button", "Save address", quarantined=False) is None
    assert (
        approval_for_click("button", "Save address", quarantined=True) is ApprovalKind.WRITE_AFTER_INJECTION
    )
    assert approval_for_click("textbox", "Delete reason", quarantined=False) is None


def test_verification_walls_are_recognised() -> None:
    assert is_verification_wall('- heading "Security check" [level=1] [ref=e7]')
    assert is_verification_wall('- textbox "Verification code" [ref=e9]')
    assert not is_verification_wall('- heading "Member Search" [level=1] [ref=e7]')
