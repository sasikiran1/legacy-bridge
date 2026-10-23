"""Circuit-breaker recovery timeline (ADR 0004): wasted API calls during an outage vs. time back on the API.

    uv run python -m analysis.breaker_recovery

A task needing `getMember` arrives every INTERVAL simulated seconds. From OUTAGE_START, for each length in
OUTAGE_LENGTHS, the portal's chaos endpoint breaks that operation; the run continues AFTER seconds past the
outage. Each arrival asks the endpoint's breaker: if it allows the call, the real API executor calls the real
portal (through the proxy's masking, like the agent); otherwise the task would go to the browser and nothing
is called. Time is simulated, the portal is not: chaos is switched at the simulated outage boundaries, and
every API outcome comes from a real HTTP response.

Compared: breakers given as (initial open interval, cap on the doubled interval). An initial interval of 0 s
makes every call a probe, i.e. no breaker. Outages are a transient failure (500) and a removed endpoint (404).
Results hold outcomes and counts only.

Probe times are fixed by the doubling, so time back on the API after one outage depends on where that outage
happens to end relative to the next probe. The `simulated` section therefore replays every outage length from
SWEEP_MIN to SWEEP_MAX in INTERVAL steps through the same Breaker code (failure while the outage lasts,
success after), and reports the mean and worst case; the portal runs validate that real 500/404 responses
drive the breaker the same way.
"""

from __future__ import annotations

import asyncio
import statistics
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
OUTAGE_START = 300.0
OUTAGE_LENGTHS = (120.0, 600.0, 1200.0)
AFTER = 900.0
SWEEP_MIN = 60.0
SWEEP_MAX = 1800.0
BREAKERS = (
    (0.0, 600.0),
    (10.0, 600.0),
    (30.0, 600.0),
    (60.0, 600.0),
    (120.0, 600.0),
    (10.0, 60.0),
    (10.0, 120.0),
    (30.0, 60.0),
    (30.0, 120.0),
)
OUTAGES = (ApiMode.SERVER_ERROR, ApiMode.NOT_FOUND)
TASK_ID = "experiment/breaker-recovery"


class SimClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def timeline(
    breaker: tuple[float, float],
    outage: ApiMode,
    length: float,
    admin: PortalAdmin,
    make_executor: Any,
    credentials: tuple[str, str],
    member_number: str,
) -> list[dict[str, Any]]:
    clock = SimClock()
    base, cap = breaker
    executor: ApiExecutor = make_executor(
        BreakerRegistry(clock=clock, base_open_seconds=base, max_open_seconds=cap)
    )
    await admin.apply(ChaosConfig())
    broken = False
    events = []
    try:
        for step in range(int((OUTAGE_START + length + AFTER) / INTERVAL)):
            clock.now = step * INTERVAL
            should_break = OUTAGE_START <= clock.now < OUTAGE_START + length
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


def simulate(breaker: tuple[float, float], length: float) -> list[dict[str, Any]]:
    """The portal timeline without the portal: the breaker sees failure during the outage, success after."""
    clock = SimClock()
    registry = BreakerRegistry(clock=clock, base_open_seconds=breaker[0], max_open_seconds=breaker[1])
    events = []
    for step in range(int((OUTAGE_START + length + AFTER) / INTERVAL)):
        clock.now = step * INTERVAL
        broken = OUTAGE_START <= clock.now < OUTAGE_START + length
        gate = registry.get(OPERATION)
        if gate.allow():
            gate.record(not broken)
            outcome = "api_failed" if broken else "api_ok"
        else:
            outcome = "browser"
        events.append({"t": clock.now, "broken": broken, "outcome": outcome})
    return events


def phase_sweep() -> dict[str, Any]:
    lengths = [SWEEP_MIN + INTERVAL * i for i in range(int((SWEEP_MAX - SWEEP_MIN) / INTERVAL) + 1)]
    summary = {}
    for breaker in BREAKERS:
        runs = [measure(simulate(breaker, length), length) for length in lengths]
        returns = [r["seconds_to_return_to_api"] for r in runs]
        summary[f"open {breaker[0]:g}s, cap {breaker[1]:g}s"] = {
            "outage_lengths": len(lengths),
            "mean_seconds_to_return": round(statistics.mean(returns), 1),
            "max_seconds_to_return": max(returns),
            "mean_browser_after_recovery": round(
                statistics.mean(r["browser_after_recovery"] for r in runs), 1
            ),
            "mean_failed_api_calls": round(statistics.mean(r["failed_api_calls_total"] for r in runs), 1),
            "max_failed_api_calls": max(r["failed_api_calls_total"] for r in runs),
        }
    return summary


def measure(events: list[dict[str, Any]], length: float) -> dict[str, Any]:
    end = OUTAGE_START + length
    after = [e for e in events if e["t"] >= end]
    back = next((e["t"] for e in after if e["outcome"] == "api_ok"), None)
    return {
        "failed_api_calls_during_outage": sum(e["broken"] and e["outcome"] == "api_failed" for e in events),
        "browser_during_outage": sum(e["broken"] and e["outcome"] == "browser" for e in events),
        "browser_after_recovery": sum(e["outcome"] == "browser" for e in after),
        "seconds_to_return_to_api": None if back is None else back - end,
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
                for length in OUTAGE_LENGTHS:
                    for breaker in BREAKERS:
                        events = await timeline(
                            breaker,
                            outage,
                            length,
                            admin,
                            make_executor,
                            (task.user, settings.test_user_password),
                            task.member_number,
                        )
                        figures = measure(events, length)
                        key = f"open {breaker[0]:g}s, cap {breaker[1]:g}s"
                        results.setdefault(outage.value, {}).setdefault(f"{length:g}s", {})[key] = {
                            **figures,
                            "outcomes": [e["outcome"] for e in events],
                        }
                        print(f"{outage.value:13} outage {length:5g}s  {key:22} {figures}")
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
            "outage_start_s": OUTAGE_START,
            "outage_lengths_s": list(OUTAGE_LENGTHS),
            "after_s": AFTER,
            "breakers_open_cap_s": [list(b) for b in BREAKERS],
            "outages": [o.value for o in OUTAGES],
            "simulated_outage_lengths_s": [SWEEP_MIN, SWEEP_MAX, INTERVAL],
        },
    )
    results = {"portal": asyncio.run(run_experiment()), "simulated": phase_sweep()}
    run.write("breaker_recovery.json", results)
    for key, figures in results["simulated"].items():
        print(f"simulated {key:22} {figures}")
    run.finish(models={})
    print(f"-> {run.directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
