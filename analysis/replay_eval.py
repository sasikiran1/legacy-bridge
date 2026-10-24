"""Compiled replay scripts against the tasks they were not compiled from (ADR 0005).

    uv run python -m analysis.replay_eval [--source api_degraded] [--scenarios healthy,dom_drift] [--write]

Compiles one script per write intent and parameter set from the source scenario's recorded browser runs (with
`--write`, also into agent/replays/), then runs each script, with no model, on every task of the same intent
and parameter set, under each scenario. Each run is graded by the same database diff as the agent's runs.
Results hold task ids, outcomes and timings only.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from typing import Any

from agent.guardrails import ApprovalKind
from agent.replay import REPLAY_DIR, CompileError, Compiler, ReplayScript, script_key
from agent.replay_runner import run_script
from agent.settings import agent_settings
from analysis.manifest import Run
from proxy.cassettes import CASSETTE_DIR
from proxy.policy import load_policy
from proxy.settings import load_settings
from proxy.tokens import TokenMinter
from testbed.portal import PortalAdmin
from testbed.postconditions import check, snapshot
from testbed.scenarios import load_scenarios
from testbed.settings import settings as testbed_settings
from testbed.tasks import Intent, Task, load_tasks

READ_INTENTS = {Intent.CLAIM_STATUS, Intent.POLICY_LOOKUP}


def compile_scripts(source: str, tasks: tuple[Task, ...]) -> tuple[dict[str, ReplayScript], dict[str, str]]:
    proxy = load_settings()
    compiler = Compiler(TokenMinter(proxy.token_key, proxy.run_seed), list(load_policy().entities))
    scripts: dict[str, ReplayScript] = {}
    errors: dict[str, str] = {}
    for task in tasks:
        cassettes = CASSETTE_DIR / f"{source}_{task.id}"
        key = script_key(task.intent.value, task.params)
        if task.intent in READ_INTENTS or key in scripts or not cassettes.exists():
            continue
        try:
            scripts[key] = compiler.compile(
                cassettes, f"{source}/{task.id}", task.intent.value, task.params, task.member_number
            )
        except CompileError as e:
            errors[task.id] = str(e)
    return scripts, errors


def approver(task: Task) -> Any:
    """The runner's task policy: pay only on reimbursement tasks, delete only on delete-note tasks."""
    allowed = {
        Intent.SUBMIT_REIMBURSEMENT: ApprovalKind.PAYMENT,
        Intent.DELETE_NOTE: ApprovalKind.DELETE,
    }.get(task.intent)
    return lambda kind, name: kind is allowed


async def evaluate(
    scripts: dict[str, ReplayScript], tasks: tuple[Task, ...], scenarios: list[str]
) -> list[dict[str, Any]]:
    settings = agent_settings()
    tb = testbed_settings()
    all_scenarios = load_scenarios()
    rows = []
    async with PortalAdmin(tb) as admin:
        try:
            for scenario in scenarios:
                for task in tasks:
                    script = scripts.get(script_key(task.intent.value, task.params))
                    if script is None:
                        continue
                    await admin.apply(all_scenarios[scenario].chaos)
                    await admin.reset()
                    before = await snapshot(tb.portal_db_url)
                    params = {**task.params, "member_number": task.member_number}
                    result = await run_script(
                        script,
                        params,
                        settings.portal_origin,
                        task.user,
                        settings.test_user_password,
                        approver(task),
                    )
                    graded = check(task.expected, before, await snapshot(tb.portal_db_url))
                    rows.append(
                        {
                            "scenario": scenario,
                            "task_id": task.id,
                            "script": script.key,
                            "compiled_from": script.provenance.task,
                            "outcome": result.outcome,
                            "passed": graded.passed and result.outcome == "success",
                            "wrong_action": graded.wrong_action,
                            "claimed_success_but_failed": result.outcome == "success" and not graded.passed,
                            "failed_step": result.failed_step,
                            "error": result.error,
                            "seconds": result.seconds,
                            "llm_calls": 0,
                            "missing": list(graded.missing),
                            "unexpected": list(graded.unexpected),
                        }
                    )
                    print(
                        f"{scenario:10} {task.id:28} {result.outcome:11} passed={rows[-1]['passed']!s:5} "
                        f"wrong={graded.wrong_action!s:5} {result.seconds:5.1f}s {result.error or ''}"
                    )
        finally:
            await admin.apply(all_scenarios["healthy"].chaos)
    return rows


def summarise(rows: list[dict[str, Any]], scenario: str) -> dict[str, Any]:
    selected = [r for r in rows if r["scenario"] == scenario]
    return {
        "runs": len(selected),
        "passed": sum(r["passed"] for r in selected),
        "wrong_actions": sum(r["wrong_action"] for r in selected),
        "claimed_success_but_failed": sum(r["claimed_success_but_failed"] for r in selected),
        "step_failed": sum(r["outcome"] == "step_failed" for r in selected),
        "median_seconds": statistics.median(r["seconds"] for r in selected) if selected else None,
        "llm_calls": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analysis.replay_eval")
    parser.add_argument("--source", default="api_degraded")
    parser.add_argument("--scenarios", default="healthy,dom_drift")
    parser.add_argument(
        "--write", action="store_true", help="also write the compiled scripts to agent/replays/"
    )
    args = parser.parse_args(argv)
    scenarios = args.scenarios.split(",")
    tasks = load_tasks()
    scripts, errors = compile_scripts(args.source, tasks)
    if args.write:
        REPLAY_DIR.mkdir(exist_ok=True)
        for key, script in scripts.items():
            (REPLAY_DIR / f"{key}.yaml").write_text(script.dump(), encoding="utf-8")
    run = Run(kind="replay-eval", config={"source": args.source, "scenarios": scenarios})
    rows = asyncio.run(evaluate(scripts, tasks, scenarios))
    run.write(
        "replay_eval.json",
        {
            "scripts": {
                k: {"compiled_from": s.provenance.task, "steps": len(s.steps)} for k, s in scripts.items()
            },
            "compile_errors": errors,
            "summary": {s: summarise(rows, s) for s in scenarios},
            "runs": rows,
        },
    )
    run.finish(models={"replay": "none (no model calls)"})
    for scenario in scenarios:
        print(scenario, summarise(rows, scenario))
    print(f"compiled {len(scripts)} scripts; compile errors: {errors}")
    print(f"-> {run.directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
