"""Runs testbed tasks through the agent and records outcomes against the portal's ground truth.

    uv run python -m agent.runner --scenario api_degraded --tasks update_address-01,claim_status-02
    uv run python -m agent.runner --scenario healthy --all --record      # LLM_MODE=record on the proxy
    uv run python -m agent.runner --scenario healthy --check-replay      # LLM_MODE=replay; CI's gate

Per task: apply the scenario's chaos, reset the seed, clear the task's tokens in the proxy, mask the
instruction, run the graph (resuming interrupts with the approval policy below), then diff the database
against the task's expectation. Rows go to analysis/results/<run_id>/agent_runs.jsonl with a manifest, except
for --check-replay.

Approval policy (the stand-in for the human reviewer):
    task             approve a payment only on reimbursement tasks, a delete only on delete-note tasks, and a
                     write after a flagged injection only on tasks that are writes; decline everything else
    always / never   for experiments on the approval gate itself
Verification walls are never solved; with --resolve-walls a person is simulated clearing the wall via the
admin endpoint, which is how the human-handoff path is exercised.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import psycopg
import redis.asyncio as aioredis
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict

from agent import openapi
from agent.breaker import BreakerRegistry
from agent.executors import ApiExecutor, BrowserSession
from agent.graph import Runtime, build_graph
from agent.guardrails import ApprovalKind, build_scanner
from agent.llm import LlmClient, LlmError
from agent.masking import MaskClient, MaskingUnavailable
from agent.settings import AgentSettings, agent_settings
from agent.telemetry import flush, tracer
from analysis.manifest import Run
from proxy.audit import AuditLog
from proxy.cassettes import CASSETTE_DIR
from proxy.policy import load_policy
from proxy.rehydrate import Rehydrator, ToolArgumentResolver
from proxy.vault import RedisVault, Sealer
from testbed.portal import PortalAdmin
from testbed.postconditions import check, snapshot
from testbed.scenarios import Scenario, load_scenarios
from testbed.settings import settings as testbed_settings
from testbed.tasks import Intent, Task, load_tasks

AGENT_AUDIT = Path(__file__).resolve().parent.parent / ".cache" / "audit" / "agent.jsonl"
CASSETTE_INDEX = CASSETTE_DIR / "index.json"
READ_INTENTS = {Intent.CLAIM_STATUS, Intent.POLICY_LOOKUP}
REPLAY_FIELDS = ("outcome", "passed", "wrong_action", "tiers", "llm_calls")


class TaskRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    intent: str
    scenario: str
    outcome: str
    passed: bool
    wrong_action: bool
    verified: bool | None
    tiers: list[str]
    fallback_reason: str | None
    handoff_reason: str | None
    api_failures: list[dict[str, Any]]
    approvals: list[dict[str, Any]]
    quarantined: bool
    llm_calls: int
    tokens_in: int
    tokens_out: int
    cassette_mismatches: int
    models: list[str]
    seconds: float
    missing: list[str]
    unexpected: list[str]
    error: str | None = None


def decide(task: Task, request: dict[str, Any], policy: str) -> dict[str, Any]:
    if policy in ("always", "never"):
        return {"approved": policy == "always"}
    kinds = {action.get("approval") for action in request.get("actions", [])}
    allowed = set()
    if task.intent is Intent.SUBMIT_REIMBURSEMENT:
        allowed.add(ApprovalKind.PAYMENT.value)
    if task.intent is Intent.DELETE_NOTE:
        allowed.add(ApprovalKind.DELETE.value)
    if task.intent not in READ_INTENTS:
        allowed.add(ApprovalKind.WRITE_AFTER_INJECTION.value)
    return {"approved": kinds <= allowed}


def load_index() -> dict[str, dict[str, Any]]:
    if not CASSETTE_INDEX.exists():
        return {}
    index: dict[str, dict[str, Any]] = json.loads(CASSETTE_INDEX.read_text(encoding="utf-8"))
    return index


def record_index(rows: list[TaskRun], scanner: str = "none") -> None:
    """Stores what each recorded task did, and with which injection scanner, so a replay can be checked."""
    index = load_index()
    for row in rows:
        index[f"{row.scenario}/{row.task_id}"] = {
            **row.model_dump(include=set(REPLAY_FIELDS)),
            "scanner": scanner,
        }
    CASSETTE_INDEX.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def replay_differences(rows: list[TaskRun]) -> list[str]:
    index = load_index()
    differences = []
    for row in rows:
        recorded = index.get(f"{row.scenario}/{row.task_id}")
        if recorded is None:
            differences.append(f"{row.task_id}: no recording")
            continue
        replayed = row.model_dump(include=set(REPLAY_FIELDS))
        differences += [
            f"{row.task_id}: {field} recorded {recorded.get(field)!r}, replayed {replayed[field]!r}"
            for field in REPLAY_FIELDS
            if recorded.get(field) != replayed[field]
        ]
    return differences


async def ollama_version() -> str:
    """The local runtime's version, so runs on different Ollama releases are never compared unknowingly."""
    base = os.environ.get("OLLAMA_BASE_URL", "").removesuffix("/").removesuffix("/v1")
    if not base:
        return "unknown"
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            response = await http.get(f"{base}/api/version")
            return f"ollama {response.json()['version']}"
    except httpx.HTTPError, ValueError, KeyError:
        return "unknown"


async def ensure_agent_database(url: str) -> None:
    dbname = url.rsplit("/", 1)[-1]
    server = url.rsplit("/", 1)[0] + "/postgres"
    async with await psycopg.AsyncConnection.connect(server, autocommit=True) as conn:
        exists = await (
            await conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        ).fetchone()
        if not exists:
            await conn.execute(f'CREATE DATABASE "{dbname}"')


async def run_task(
    task: Task,
    scenario: Scenario,
    settings: AgentSettings,
    services: dict[str, Any],
    run_id: str,
    approvals: str,
    resolve_walls: bool,
    record: bool,
) -> TaskRun:
    admin: PortalAdmin = services["admin"]
    task_key = f"{scenario.name}/{task.id}"
    await admin.apply(scenario.chaos)
    await admin.reset()
    await services["proxy_http"].delete(f"/v1/tasks/{task_key}")
    if record:
        shutil.rmtree(CASSETTE_DIR / task_key.replace("/", "_"), ignore_errors=True)
    before = await snapshot(services["db_url"])

    masker: MaskClient = services["masker"]

    async def open_browser() -> BrowserSession:
        session = BrowserSession(settings.portal_origin, settings.portal_host, services["resolver"], masker)
        return await session.start()

    runtime = Runtime(
        settings=settings,
        llm=services["llm"],
        api=services["api"],
        masker=masker,
        scanner=services["scanner"],
        password=settings.test_user_password,
        open_browser=open_browser,
    )
    graph = build_graph(runtime, services["checkpointer"])
    config: RunnableConfig = {"configurable": {"thread_id": f"{run_id}/{task_key}"}}
    started = time.perf_counter()
    state: dict[str, Any] = {}
    error = None
    try:
        with tracer().start_as_current_span(f"task {task_key}"):
            payload: Any = {
                "task_key": task_key,
                "username": task.user,
                "instruction": await masker.text(task_key, task.instruction),
            }
            while True:
                state = await graph.ainvoke(payload, config)
                pending = state.get("__interrupt__")
                if not pending:
                    break
                request = pending[0].value
                if request.get("kind") == "approval":
                    payload = Command(resume=decide(task, request, approvals))
                elif resolve_walls and "wall" in str(request.get("reason", "")):
                    await admin.clear_wall(task.user)
                    payload = Command(resume={"resolved": True})
                else:
                    payload = Command(resume={"resolved": False})
    except (LlmError, MaskingUnavailable, httpx.HTTPError) as e:
        error = str(e)[:300]
    finally:
        await runtime.close()
    seconds = time.perf_counter() - started

    answer = state.get("answer")
    if answer:
        answer = await services["rehydrator"].text(answer, task_key, "operator")
    result = check(task.expected, before, await snapshot(services["db_url"]), answer)
    outcome = "error" if error else (state.get("outcome") or "incomplete")
    return TaskRun(
        task_id=task.id,
        intent=task.intent.value,
        scenario=scenario.name,
        outcome=outcome,
        passed=result.passed and outcome == "success",
        wrong_action=result.wrong_action,
        verified=state.get("verified"),
        tiers=list(state.get("tiers", [])),
        fallback_reason=state.get("fallback_reason"),
        handoff_reason=state.get("handoff_reason"),
        api_failures=list(state.get("api_failures", [])),
        approvals=list(state.get("approvals", [])),
        quarantined=bool(state.get("quarantined")),
        llm_calls=int(state.get("llm_calls", 0)),
        tokens_in=int(state.get("tokens_in", 0)),
        tokens_out=int(state.get("tokens_out", 0)),
        cassette_mismatches=int(state.get("cassette_mismatches", 0)),
        models=list(state.get("models", [])),
        seconds=round(seconds, 2),
        missing=list(result.missing),
        unexpected=list(result.unexpected),
        error=error,
    )


async def main_async(args: argparse.Namespace) -> int:
    settings = agent_settings()
    tb = testbed_settings()
    scenario = load_scenarios()[args.scenario]
    tasks = load_tasks()
    if args.check_replay and not args.tasks:
        recorded = {
            key.split("/", 1)[1]: entry.get("scanner", "none")
            for key, entry in load_index().items()
            if key.startswith(f"{scenario.name}/")
        }
        # A scanner flag changes which writes need approval, so a replay under another scanner is not the
        # same run; those tasks are skipped and counted, never passed (CI has no Prompt Guard; ADR 0014).
        skipped = sorted(t for t, s in recorded.items() if s != settings.injection_scanner)
        if skipped:
            names = ", ".join(skipped)
            print(f"skipped {len(skipped)} tasks recorded with another injection scanner: {names}")
        tasks = tuple(t for t in tasks if recorded.get(t.id) == settings.injection_scanner)
    elif not args.all:
        wanted = set(args.tasks.split(","))
        tasks = tuple(t for t in tasks if t.id in wanted)
    policy = load_policy()
    redis_client = aioredis.Redis.from_url(settings.redis_url)
    vault = RedisVault(redis_client, Sealer(settings.vault_master_key), settings.vault_task_ttl_seconds)
    audit = AuditLog(AGENT_AUDIT)
    resolver = ToolArgumentResolver(vault, audit, policy, frozenset({settings.portal_host}))
    masker = MaskClient(settings.proxy_url)
    proxy_http = httpx.AsyncClient(base_url=settings.proxy_url, timeout=60)
    spec = (await httpx.AsyncClient().get(f"{settings.portal_url}/v3/api-docs")).json()
    api = ApiExecutor(
        settings.portal_url, settings.portal_host, openapi.parse(spec), BreakerRegistry(), resolver, masker
    )
    await ensure_agent_database(settings.agent_db_url)

    run = Run(
        kind="agent-run",
        config={
            "scenario": scenario.name,
            "tasks": [t.id for t in tasks],
            "profile": settings.profile.value,
            "approvals": args.approvals,
            "resolve_walls": args.resolve_walls,
            "scanner": settings.injection_scanner,
            "record": args.record,
            "label": args.label,
        },
    )
    rows: list[TaskRun] = []
    async with PortalAdmin(tb) as admin, AsyncPostgresSaver.from_conn_string(settings.agent_db_url) as saver:
        await saver.setup()
        services: dict[str, Any] = {
            "admin": admin,
            "db_url": tb.portal_db_url,
            "masker": masker,
            "llm": LlmClient(settings.proxy_url),
            "api": api,
            "resolver": resolver,
            "scanner": build_scanner(settings.injection_scanner),
            "checkpointer": saver,
            "proxy_http": proxy_http,
            "rehydrator": Rehydrator(vault, audit),
        }
        for task in tasks:
            row = await run_task(
                task,
                scenario,
                settings,
                services,
                run.run_id,
                args.approvals,
                args.resolve_walls,
                args.record,
            )
            rows.append(row)
            print(
                f"{row.task_id:28} {row.outcome:11} passed={row.passed!s:5} wrong={row.wrong_action!s:5} "
                f"tiers={'>'.join(row.tiers):12} llm={row.llm_calls:2} {row.seconds:6.1f}s "
                f"{row.fallback_reason or ''}{(' | ' + row.error) if row.error else ''}"
            )
        await services["llm"].aclose()
        await admin.apply(load_scenarios()["healthy"].chaos)

    destination = "not saved (replay check)"
    if not args.check_replay:
        # A replay check is a gate, not an experiment: its rows repeat the recording's and are not kept.
        run.write("breakers.json", api.breakers.snapshot())
        (run.directory / "agent_runs.jsonl").write_text(
            "".join(r.model_dump_json() + "\n" for r in rows), encoding="utf-8"
        )
        served = sorted({m for r in rows for m in r.models})
        run.finish(
            models={
                "profile": settings.profile.value,
                "served": ", ".join(served),
                "runtime": await ollama_version(),
            }
        )
        destination = str(run.directory)
    await masker.aclose()
    await proxy_http.aclose()
    await api.aclose()
    await redis_client.aclose()
    flush()
    passed = sum(r.passed for r in rows)
    print(
        f"{passed}/{len(rows)} passed, {sum(r.wrong_action for r in rows)} wrong actions, "
        f"{sum(r.cassette_mismatches for r in rows)} cassette mismatches -> {destination}"
    )
    if args.record:
        record_index(rows, settings.injection_scanner)
    if args.check_replay:
        differences = replay_differences(rows)
        for line in differences:
            print(f"replay differs: {line}")
        return 1 if differences or not rows else 0
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent.runner")
    parser.add_argument("--scenario", default="healthy")
    parser.add_argument("--tasks", default="")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--approvals", choices=("task", "always", "never"), default="task")
    parser.add_argument("--resolve-walls", action="store_true")
    parser.add_argument(
        "--record", action="store_true", help="clear this task's cassettes first (proxy in record mode)"
    )
    parser.add_argument(
        "--check-replay",
        action="store_true",
        help="run the scenario's recorded tasks (proxy in replay mode) and fail if any outcome differs",
    )
    parser.add_argument("--label", default="")
    args = parser.parse_args(argv)
    if args.record and args.check_replay:
        parser.error("--record and --check-replay are exclusive")
    if not args.all and not args.tasks and not args.check_replay:
        parser.error("pass --tasks, --all or --check-replay")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
