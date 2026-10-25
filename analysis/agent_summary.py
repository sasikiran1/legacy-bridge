"""Agent results per scenario, from the committed agent runs.

    uv run python -m analysis.agent_summary [--label dev]

Reads every analysis/results/*-agent-run whose manifest was a recording (`record: true`) with the given label,
keeps the latest row per (scenario, task), so a re-recorded task replaces its earlier run, and writes the
per-scenario figures with the run ids they come from.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from typing import Any

from analysis.manifest import RESULTS_DIR, Run, agent_run_manifests, agent_run_rows
from proxy.cassettes import CASSETTE_DIR


def latest_rows(label: str) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], str]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    sources: dict[tuple[str, str], str] = {}
    for directory, _ in agent_run_manifests(RESULTS_DIR, [label], recorded_only=True):
        for row in agent_run_rows(directory):
            key = (row["scenario"], row["task_id"])
            rows[key] = row
            sources[key] = directory.name
    return rows, sources


def cassette_models(row: dict[str, Any]) -> set[str]:
    """Rows recorded before runs tracked their models; the cassettes name who served each call."""
    directory = CASSETTE_DIR / f"{row['scenario']}_{row['task_id']}"
    return {
        json.loads(path.read_text(encoding="utf-8"))["served_by"]
        for path in sorted(directory.glob("[0-9][0-9][0-9].json"))
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tasks": len(rows),
        "passed": sum(r["passed"] for r in rows),
        "wrong_actions": sum(r["wrong_action"] for r in rows),
        "fell_back_to_browser": sum("explore" in r["tiers"] for r in rows),
        "claimed_success_but_failed": sum(r["outcome"] == "success" and not r["passed"] for r in rows),
        "outcomes": dict(sorted(Counter(r["outcome"] for r in rows).items())),
        "median_llm_calls": statistics.median(r["llm_calls"] for r in rows),
        "median_seconds": statistics.median(r["seconds"] for r in rows),
        "median_prompt_tokens": statistics.median(r["tokens_in"] for r in rows),
        "models": sorted({m for r in rows for m in r.get("models") or cassette_models(r)}),
        "failed_tasks": sorted(r["task_id"] for r in rows if not r["passed"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analysis.agent_summary")
    parser.add_argument("--label", default="dev")
    args = parser.parse_args(argv)
    rows, sources = latest_rows(args.label)
    scenarios = sorted({scenario for scenario, _ in rows})
    summary = {
        scenario: {
            **summarise([row for (s, _), row in sorted(rows.items()) if s == scenario]),
            "runs": sorted({run for (s, _), run in sources.items() if s == scenario}),
        }
        for scenario in scenarios
    }
    run = Run(kind="agent-summary", config={"label": args.label})
    run.write("agent_summary.json", summary)
    run.finish(models={"served": ", ".join(sorted({m for s in summary.values() for m in s["models"]}))})
    for scenario, figures in summary.items():
        print(
            f"{scenario:14} {figures['passed']}/{figures['tasks']} passed, "
            f"{figures['wrong_actions']} wrong actions, {figures['fell_back_to_browser']} via browser"
        )
    print(f"-> {run.directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
