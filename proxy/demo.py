"""M2 demo: one request through the proxy, end to end, against a recording provider.

    uv run python -m proxy.demo        # make proxy-demo

1. A canary member's task plus their API record go in raw.
2. Shows what the provider received (tokens only) and checks it for canaries.
3. Streams the provider's reply back with final rehydration; tokens split across SSE chunks are reassembled.
4. Resolves tool arguments at the execution boundary: one allowed, one refused by the rehydration policy.
5. Verifies the audit chain, then tampers with a copy and verifies again.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

import httpx
import redis.asyncio as aioredis

from proxy.app import REHYDRATE_HEADER, TASK_HEADER, Services, create_app
from proxy.audit import verify
from proxy.detection import DetectorConfig, build_detector
from proxy.offline import RECORDING_MODEL, offline_services
from proxy.policy import load_policy
from proxy.recording import RecordingProvider
from proxy.rehydrate import RehydrationDenied, ToolArgumentResolver
from proxy.tokens import TOKEN_RE
from proxy.vault import RedisVault, Sealer, Vault
from testbed.seed import generate

DEMO_DIR = Path(__file__).resolve().parent.parent / ".cache" / "demo"


def heading(text: str) -> None:
    print(f"\n== {text}")


async def run() -> int:
    ds, tasks, canaries = generate()
    task = next(t for t in tasks if t.member_number in ds.canary_member_numbers)
    member = next(m for m in ds.members if m.member_number == task.member_number)
    record = {
        "memberNumber": member.member_number,
        "firstName": member.first_name,
        "lastName": member.last_name,
        "dateOfBirth": member.date_of_birth.isoformat(),
        "email": member.email,
        "phone": member.phone,
        "address": {"street": member.street, "city": member.city, "state": member.state},
    }

    shutil.rmtree(DEMO_DIR, ignore_errors=True)
    DEMO_DIR.mkdir(parents=True)
    vault: Vault | None = None
    if os.environ.get("REDIS_URL"):
        vault = RedisVault(aioredis.Redis.from_url(os.environ["REDIS_URL"]), Sealer(bytes(range(32))), 600)
    policy = load_policy()
    print("loading detectors (spaCy en_core_web_lg + GLiNER) ...")
    provider = RecordingProvider(chunk_size=4)
    services = offline_services(
        build_detector(DetectorConfig.FULL, policy), provider, DEMO_DIR / "audit.jsonl", vault
    )
    task_id = f"demo-{task.id}"
    if vault is not None:
        await vault.drop(task_id)

    async def ready() -> Services:
        return services

    app = create_app(ready)
    app.state.services = services
    body = {
        "model": RECORDING_MODEL,
        "stream": True,
        "messages": [
            {"role": "system", "content": "You operate the member services portal."},
            {"role": "user", "content": f"{task.instruction}\nCurrent record: {json.dumps(record)}"},
        ],
    }

    heading("1. Raw request from the agent")
    print(body["messages"][1]["content"])  # type: ignore[index]

    deltas: list[str] = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy") as client:
        headers = {TASK_HEADER: task_id, REHYDRATE_HEADER: "final"}
        async with client.stream("POST", "/v1/chat/completions", json=body, headers=headers) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    for choice in json.loads(line[6:])["choices"]:
                        deltas.append(choice.get("delta", {}).get("content") or "")

    sent = json.loads(provider.bodies[0])["messages"][1]["content"]
    heading("2. What the provider received")
    print(sent)
    leaked = [c for c in canaries if c.value in provider.recorded_text()]
    print(f"\ncanary values in the outbound payload: {len(leaked)}")

    heading("3. Streamed reply, rehydrated for the operator (chunk boundaries shown as |)")
    print("|".join(d for d in deltas if d))

    heading("4. Tool arguments at the execution boundary")
    resolver = ToolArgumentResolver(services.vault, services.audit, policy, frozenset({"portal"}))
    street_token = next(t for t in _tokens(sent) if t.startswith("[[STREET_ADDRESS"))
    member_token = next(t for t in _tokens(sent) if t.startswith("[[MEMBER_ID"))
    allowed = await resolver.resolve(
        task_id, "updateAddress", {"memberNumber": member_token, "street": street_token}, target_host="portal"
    )
    print(f"updateAddress {{memberNumber: {member_token}, street: {street_token}}} -> {allowed}")
    dob_token = next(t for t in _tokens(sent) if t.startswith("[[DATE_OF_BIRTH"))
    try:
        await resolver.resolve(
            task_id, "addNote", {"memberNumber": member_token, "body": f"DOB is {dob_token}"}
        )
    except RehydrationDenied as e:
        print(f"addNote {{body: 'DOB is {dob_token}'}} -> refused: {e}")

    heading("5. Audit chain")
    audit_path = DEMO_DIR / "audit.jsonl"
    print(f"{audit_path.relative_to(DEMO_DIR.parent.parent)}: {verify(audit_path).model_dump_json()}")
    tampered = DEMO_DIR / "audit-tampered.jsonl"
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[1])
    entry["destination"] = "client"
    lines[1] = json.dumps(entry)
    tampered.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"after editing entry 2 of a copy: {verify(tampered).model_dump_json()}")
    log_text = audit_path.read_text(encoding="utf-8")
    print(f"raw canary values in the audit log: {sum(c.value in log_text for c in canaries)}")

    await services.http.aclose()
    if vault is not None:
        await vault.drop(task_id)
    return 0 if not leaked else 1


def _tokens(text: str) -> list[str]:
    return [m.group() for m in TOKEN_RE.finditer(text)]


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
