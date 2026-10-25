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
from agent.guardrails import (
    ApprovalKind,
    InjectionScanner,
    RecordingScanner,
    ReplayScanner,
    build_scanner,
)
from agent.llm import LlmClient, LlmError
from agent.masking import MaskClient, MaskingUnavailable
from agent.replay import REPLAY_DIR, CompileError, Compiler, ReplayScript, load_script, script_key
from agent.replay_runner import ReplayResult, run_script
from agent.settings import AgentSettings, agent_settings
from agent.telemetry import flush, tracer
from analysis.manifest import Run
from proxy.audit import AuditLog
from proxy.cassettes import CASSETTE_DIR
from proxy.policy import load_policy
from proxy.rehydrate import Rehydrator, ToolArgumentResolver
from proxy.settings import load_settings as load_proxy_settings
from proxy.tokens import TokenMinter
from proxy.vault import RedisVault, Sealer
from testbed.portal import PortalAdmin
from testbed.postconditions import check, snapshot
from testbed.scenarios import Scenario, load_scenarios
from testbed.settings import settings as testbed_settings
from testbed.tasks import Intent, Task, load_tasks

AGENT_AUDIT = Path(__file__).resolve().parent.parent / ".cache" / "audit" / "agent.jsonl"
CASSETTE_INDEX = CASSETTE_DIR / "index.json"
READ_INTENTS = {Intent.CLAIM_STATUS, Intent.POLICY_LOOKUP}
REPLAY_FIELDS = ("outcome", "passed", "wrong_action", "tiers", "llm_calls", "quarantined")


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
    llm_seconds: float = 0.0
    """Time spent waiting on the model (local GPU time for Ollama), reported beside the $0 notional cost."""
    cassette_mismatches: int
    models: list[str]
    seconds: float
    missing: list[str]
    unexpected: list[str]
    error: str | None = None
    replay_outcome: str | None = None
    compiled: str | None = None


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


def record_index(rows: list[TaskRun], scanner: str = "none", scripts: bool = False) -> None:
    """Stores what each recorded task did, with which injection scanner and whether compiled scripts were on,
    so a replay can be checked under the same conditions."""
    index = load_index()
    for row in rows:
        index[f"{row.scenario}/{row.task_id}"] = {
            **row.model_dump(include=set(REPLAY_FIELDS)),
            "scanner": scanner,
            "scripts": scripts,
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
            if field in recorded and recorded[field] != replayed[field]
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
    replay: bool = False,
    use_scripts: bool = False,
    compile_scripts: bool = False,
) -> TaskRun:
    admin: PortalAdmin = services["admin"]
    task_key = f"{scenario.name}/{task.id}"
    scans = CASSETTE_DIR / task_key.replace("/", "_") / "scans.json"
    scanner: InjectionScanner = services["scanner"]
    if record:
        scanner = RecordingScanner(scanner, scans)
    elif replay:
        scanner = ReplayScanner(scans, scanner.name)
    await admin.apply(scenario.chaos)
    await admin.reset()
    await services["proxy_http"].delete(f"/v1/tasks/{task_key}")
    if record:
        shutil.rmtree(CASSETTE_DIR / task_key.replace("/", "_"), ignore_errors=True)
    before = await snapshot(services["db_url"])

    masker: MaskClient = services["masker"]

    async def open_browser() -> BrowserSession:
        session = BrowserSession(
            settings.portal_origin,
            settings.portal_host,
            services["resolver"],
            masker,
            trim=settings.snapshots == "trimmed",
        )
        return await session.start()

    script_path = REPLAY_DIR / f"{script_key(task.intent.value, task.params)}.yaml"
    script = load_script(script_path) if use_scripts and script_path.exists() else None

    async def run_compiled() -> ReplayResult:
        assert script is not None

        def approve(kind: ApprovalKind, name: str) -> bool:
            return bool(decide(task, {"actions": [{"approval": kind.value}]}, approvals)["approved"])

        params = {**task.params, "member_number": task.member_number}
        return await run_script(
            script, params, settings.portal_origin, task.user, settings.test_user_password, approve
        )

    runtime = Runtime(
        settings=settings,
        llm=services["llm"],
        api=services["api"],
        masker=masker,
        scanner=scanner,
        password=settings.test_user_password,
        open_browser=open_browser,
        replay=run_compiled if script is not None else None,
        replay_hint=describe(script) if script is not None else "",
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
    compiled = None
    if compile_scripts and outcome == "success" and "explore" in state.get("tiers", []):
        # Heal or learn: a browser run the agent verified becomes the next version of the intent's script.
        compiled = compile_script(task, task_key, script)
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
        llm_seconds=float(state.get("llm_seconds", 0.0)),
        cassette_mismatches=int(state.get("cassette_mismatches", 0)),
        models=list(state.get("models", [])),
        seconds=round(seconds, 2),
        missing=list(result.missing),
        unexpected=list(result.unexpected),
        error=error,
        replay_outcome=state.get("replay_outcome"),
        compiled=compiled,
    )


def compile_script(task: Task, task_key: str, previous: ReplayScript | None) -> str:
    """Compiles this task's cassettes into a script; returns 'key vN' or why it could not compile."""
    proxy = load_proxy_settings()
    compiler = Compiler(TokenMinter(proxy.token_key, proxy.run_seed), list(load_policy().entities))
    try:
        script = compiler.compile(
            CASSETTE_DIR / task_key.replace("/", "_"),
            task_key,
            task.intent.value,
            task.params,
            task.member_number,
        )
    except CompileError as e:
        return f"not compiled: {e}"[:300]
    if previous is not None:
        script = script.model_copy(update={"version": previous.version + 1})
    REPLAY_DIR.mkdir(exist_ok=True)
    (REPLAY_DIR / f"{script.key}.yaml").write_text(script.dump(), encoding="utf-8")
    return f"{script.key} v{script.version}"


def describe(script: ReplayScript) -> str:
    """The script's steps as templates, for the browser tier's hint; no parameter values."""
    parts = []
    for step in script.steps:
        target = f"{step.target.role} '{step.target.name}'" if step.target else ""
        parts.append(
            {
                "navigate": f"open {step.url}",
                "click": f"click {target}",
                "type": f"type {step.value} into {target}",
                "select": f"choose {step.value} in {target}",
                "press": f"press {step.value}",
            }[step.action]
        )
    return "; ".join(parts)


def save(run: Run, rows: list[TaskRun], api: ApiExecutor, settings: AgentSettings, runtime: str) -> None:
    run.write("breakers.json", api.breakers.snapshot())
    (run.directory / "agent_runs.jsonl").write_text(
        "".join(r.model_dump_json() + "\n" for r in rows), encoding="utf-8"
    )
    served = sorted({m for r in rows for m in r.models})
    run.finish(models={"profile": settings.profile.value, "served": ", ".join(served), "runtime": runtime})


async def main_async(args: argparse.Namespace) -> int:
    settings = agent_settings()
    tb = testbed_settings()
    scenario = load_scenarios()[args.scenario]
    tasks = load_tasks()
    if args.check_replay and not args.tasks:
        recorded = {key.split("/", 1)[1] for key in load_index() if key.startswith(f"{scenario.name}/")}
        tasks = tuple(t for t in tasks if t.id in recorded)
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
    async with PortalAdmin(tb) as admin:
        # Compose starts the portal without waiting for Spring Boot; CI once fetched the spec too early.
        await admin.wait_ready()
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
            "snapshots": settings.snapshots,
            "record": args.record,
            "label": args.label,
        },
    )
    rows: list[TaskRun] = []
    runtime = await ollama_version()
    async with PortalAdmin(tb) as admin, AsyncPostgresSaver.from_conn_string(settings.agent_db_url) as saver:
        await saver.setup()
        services: dict[str, Any] = {
            "admin": admin,
            "db_url": tb.portal_db_url,
            "masker": masker,
            "llm": LlmClient(settings.proxy_url),
            "api": api,
            "resolver": resolver,
            # A replay check serves the recorded verdicts (ReplayScanner), so it never needs the gated model.
            "scanner": build_scanner("none" if args.check_replay else settings.injection_scanner),
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
                args.check_replay,
                # A replay check runs scripts only if the recording did, or the model calls fall out of step.
                load_index().get(f"{scenario.name}/{task.id}", {}).get("scripts", False)
                if args.check_replay
                else args.scripts,
                args.compile,
            )
            rows.append(row)
            print(
                f"{row.task_id:28} {row.outcome:11} passed={row.passed!s:5} wrong={row.wrong_action!s:5} "
                f"tiers={'>'.join(row.tiers):12} llm={row.llm_calls:2} {row.seconds:6.1f}s "
                f"{row.fallback_reason or ''}{(' | ' + row.error) if row.error else ''}",
                flush=True,
            )
            if not args.check_replay:
                # Saved after every task, so an interrupted run keeps what it finished; the manifest's
                # config.tasks against the rows shows how far it got.
                save(run, rows, api, settings, runtime)
                if args.record:
                    record_index([row], settings.injection_scanner, args.scripts)
        await services["llm"].aclose()
        await admin.apply(load_scenarios()["healthy"].chaos)

    # A replay check is a gate, not an experiment: its rows repeat the recording's and are not kept.
    destination = "not saved (replay check)" if args.check_replay else str(run.directory)
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
    parser.add_argument(
        "--scripts", action="store_true", help="run a compiled replay script when the API tier falls back"
    )
    parser.add_argument(
        "--compile",
        action="store_true",
        help="compile each verified browser run into a script (with --record)",
    )
    parser.add_argument("--label", default="")
    args = parser.parse_args(argv)
    if args.record and args.check_replay:
        parser.error("--record and --check-replay are exclusive")
    if args.compile and not args.record:
        parser.error("--compile needs --record: scripts are compiled from the recorded cassettes")
    if not args.all and not args.tasks and not args.check_replay:
        parser.error("pass --tasks, --all or --check-replay")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
