"""Probe every public API operation under every scenario and print the outcome matrix.

    uv run python -m testbed.probe            # inside the testbed container: make probe

Cells show the HTTP status, plus `drift` when a 2xx body no longer has the current schema's key field and
`deprecated` when the response carries a Deprecation header. The database is reset after each scenario.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

import httpx

from testbed.dataset import ClaimStatus
from testbed.portal import PortalAdmin
from testbed.scenarios import Operation, load_scenarios
from testbed.seed import generate
from testbed.settings import settings


@dataclass(frozen=True)
class Probe:
    operation: Operation
    method: str
    path: str
    body: dict[str, str] | None = None
    schema_key: str | None = None


def probes() -> list[Probe]:
    ds, _, _ = generate()
    member = ds.members[0]
    policy = ds.policies_of(member)[0]
    denied = next(c for c in ds.claims if c.status is ClaimStatus.DENIED)
    payable = next(c for c in ds.claims if c.status is ClaimStatus.APPROVED)
    payee = ds.owner_of_claim(payable)
    note = next(n for n in ds.notes if not n.injected)
    m = member.member_number
    return [
        Probe(
            "searchMembers", "GET", f"/api/v1/members?lastName={member.last_name}", schema_key="memberNumber"
        ),
        Probe("getMember", "GET", f"/api/v1/members/{m}", schema_key="memberNumber"),
        Probe(
            "updateAddress",
            "PUT",
            f"/api/v1/members/{m}/address",
            {"street": "1 Probe St", "city": "Albany", "state": "NY", "postalCode": "12207"},
            schema_key="memberNumber",
        ),
        Probe("updateContact", "PATCH", f"/api/v1/members/{m}/contact", {"phone": "(518) 555-0100"}),
        Probe("listPolicies", "GET", f"/api/v1/members/{m}/policies", schema_key="policyNumber"),
        Probe("getPolicy", "GET", f"/api/v1/policies/{policy.policy_number}", schema_key="policyNumber"),
        Probe("listClaims", "GET", f"/api/v1/members/{m}/claims", schema_key="claimNumber"),
        Probe("getClaim", "GET", f"/api/v1/claims/{denied.claim_number}", schema_key="claimNumber"),
        Probe("requestReconsideration", "POST", f"/api/v1/claims/{denied.claim_number}/reconsideration"),
        Probe(
            "submitReimbursement",
            "POST",
            "/api/v1/reimbursements",
            {
                "memberNumber": payee.member_number,
                "claimNumber": payable.claim_number,
                "amount": "10.00",
                "payeeName": payee.full_name,
                "bankRouting": "021000021",
                "bankAccount": "123456789",
            },
        ),
        Probe("listNotes", "GET", f"/api/v1/members/{m}/notes", schema_key="id"),
        Probe("addNote", "POST", f"/api/v1/members/{m}/notes", {"body": "Probe note."}, schema_key="id"),
        Probe("deleteNote", "DELETE", f"/api/v1/notes/{note.id}"),
    ]


def describe(response: httpx.Response, probe: Probe) -> str:
    cell = str(response.status_code)
    if response.is_success and probe.schema_key and response.content:
        body = response.json()
        sample = body[0] if isinstance(body, list) and body else body
        if isinstance(sample, dict) and probe.schema_key not in sample:
            cell += " drift"
    if response.is_success and "Deprecation" in response.headers:
        cell += " deprecated"
    return cell


async def run() -> str:
    config = settings()
    scenarios = load_scenarios()
    checks = probes()
    rows: dict[str, list[str]] = {p.operation: [] for p in checks}
    auth = ("clerk01", config.portal_test_user_password)
    async with (
        PortalAdmin(config) as admin,
        httpx.AsyncClient(base_url=config.portal_base_url, auth=auth, timeout=30) as api,
    ):
        await admin.wait_ready()
        for scenario in scenarios.values():
            await admin.apply(scenario.chaos)
            for probe in checks:
                response = await api.request(probe.method, probe.path, json=probe.body)
                rows[probe.operation].append(describe(response, probe))
            await admin.reset()
        await admin.apply(scenarios["healthy"].chaos)

    header = "| operation | " + " | ".join(scenarios) + " |"
    rule = "|---" * (len(scenarios) + 1) + "|"
    lines = [header, rule, *(f"| {op} | " + " | ".join(cells) + " |" for op, cells in rows.items())]
    return "\n".join(lines)


def main() -> int:
    print(asyncio.run(run()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
