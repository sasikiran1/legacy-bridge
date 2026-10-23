"""Circuit-breaker recovery timeline (ADR 0004): wasted API calls during an outage vs. time back on the API.

    uv run python -m analysis.breaker_recovery

A task needing `getMember` arrives every INTERVAL simulated seconds for DURATION seconds. Between OUTAGE_START
and OUTAGE_END the portal's chaos endpoint breaks that operation. Each arrival asks the endpoint's breaker: if
it allows the call, the real API executor calls the real portal (through the proxy's masking, like the agent);
otherwise the task would go to the browser and nothing is called. Time is simulated, the portal is not: chaos
is switched at the simulated outage boundaries, and every API outcome comes from a real HTTP response.

Compared: an initial open interval of 0 s (every call is a probe, i.e. no breaker) and the ADR's candidates,
for a transient outage (500) and a removed endpoint (404). Results hold outcomes and counts only.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import httpx
import redis.asyncio as aioredis

from agent import openapi
from agent.breaker import BreakerRegistry
from agent.executors import ApiExecutor
from agent.masking import MaskClient
from agent.settings import agent_settings
from analysis.manifest import REPO_ROOT, Run
from proxy.audit import AuditLog
from proxy.policy import load_policy
from proxy.rehydrate import ToolArgumentResolver
from proxy.vault import RedisVault, Sealer
from testbed.portal import PortalAdmin
from testbed.scenarios import ApiMode, ChaosConfig
from testbed.settings import settings as testbed_settings
from testbed.tasks import load_tasks

OPERATION = "getMember"
INTERVAL = 10.0
DURATION = 1800.0
OUTAGE_START = 300.0
OUTAGE_END = 900.0
OPEN_INTERVALS = (0.0, 10.0, 30.0, 60.0, 120.0)
OUTAGES = (ApiMode.SERVER_ERROR, ApiMode.NOT_FOUND)
TASK_ID = "experiment/breaker-recovery"


class SimClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def timeline(
    open_seconds: float,
    outage: ApiMode,
    admin: PortalAdmin,
    make_executor: Any,
    credentials: tuple[str, str],
    member_number: str,
) -> list[dict[str, Any]]:
    clock = SimClock()
    executor: ApiExecutor = make_executor(BreakerRegistry(clock=clock, base_open_seconds=open_seconds))
    await admin.apply(ChaosConfig())
    broken = False
    events = []
    try:
        for step in range(int(DURATION / INTERVAL)):
            clock.now = step * INTERVAL
            should_break = OUTAGE_START <= clock.now < OUTAGE_END
            if should_break != broken:
                await admin.apply(ChaosConfig(api={OPERATION: outage}) if should_break else ChaosConfig())
                broken = should_break
            if executor.breakers.get(OPERATION).allow():
                result = await executor.call(TASK_ID, credentials, OPERATION, {"memberNumber": member_number})
                outcome = "api_ok" if result.ok else "api_failed"
            else:
                outcome = "browser"
            events.append({"t": clock.now, "broken": broken, "outcome": outcome})
    finally:
        await admin.apply(ChaosConfig())
        await executor.aclose()
    return events


def measure(events: list[dict[str, Any]]) -> dict[str, Any]:
    after = [e for e in events if e["t"] >= OUTAGE_END]
    back = next((e["t"] for e in after if e["outcome"] == "api_ok"), None)
    return {
        "failed_api_calls_during_outage": sum(e["broken"] and e["outcome"] == "api_failed" for e in events),
        "browser_during_outage": sum(e["broken"] and e["outcome"] == "browser" for e in events),
        "browser_after_recovery": sum(e["outcome"] == "browser" for e in after),
        "seconds_to_return_to_api": None if back is None else back - OUTAGE_END,
        "failed_api_calls_total": sum(e["outcome"] == "api_failed" for e in events),
    }


async def run_experiment() -> dict[str, Any]:
    settings = agent_settings()
    spec = (await httpx.AsyncClient().get(f"{settings.portal_url}/v3/api-docs")).json()
    operations = openapi.parse(spec)
    redis_client = aioredis.Redis.from_url(settings.redis_url)
    vault = RedisVault(redis_client, Sealer(settings.vault_master_key), settings.vault_task_ttl_seconds)
    audit = AuditLog(REPO_ROOT / ".cache" / "audit" / "breaker-recovery.jsonl")
    resolver = ToolArgumentResolver(vault, audit, load_policy(), frozenset({settings.portal_host}))
    masker = MaskClient(settings.proxy_url)
    task = next(t for t in load_tasks() if t.id == "claim_status-01")

    def make_executor(breakers: BreakerRegistry) -> ApiExecutor:
        return ApiExecutor(settings.portal_url, settings.portal_host, operations, breakers, resolver, masker)

    results: dict[str, Any] = {}
    try:
        async with PortalAdmin(testbed_settings()) as admin:
            for outage in OUTAGES:
                for open_seconds in OPEN_INTERVALS:
                    events = await timeline(
                        open_seconds,
                        outage,
                        admin,
                        make_executor,
                        (task.user, settings.test_user_password),
                        task.member_number,
                    )
                    figures = measure(events)
                    results.setdefault(outage.value, {})[f"{open_seconds:g}"] = {
                        **figures,
                        "outcomes": [e["outcome"] for e in events],
                    }
                    print(f"{outage.value:16} open {open_seconds:5g}s  {figures}")
    finally:
        async with httpx.AsyncClient(base_url=settings.proxy_url) as proxy:
            await proxy.delete(f"/v1/tasks/{TASK_ID}")
        await masker.aclose()
        await redis_client.aclose()
    return results


def main(argv: list[str] | None = None) -> int:
    run = Run(
        kind="breaker-recovery",
        config={
            "operation": OPERATION,
            "interval_s": INTERVAL,
            "duration_s": DURATION,
            "outage_s": [OUTAGE_START, OUTAGE_END],
            "open_intervals_s": list(OPEN_INTERVALS),
            "outages": [o.value for o in OUTAGES],
        },
    )
    results = asyncio.run(run_experiment())
    run.write("breaker_recovery.json", results)
    run.finish(models={})
    print(f"-> {run.directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
