"""Local-model bake-off: the same tasks and scenarios per model, from the committed agent runs.

    uv run python -m analysis.bakeoff [--label bakeoff]

Reads every analysis/results/*-agent-run with the given label (live or recorded), groups rows by the model
that served them and the scenario, keeps the latest row per (model, scenario, task), and writes the per-model
figures with the run ids they come from. A row served by more than one model (a fallback) is reported
separately.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from analysis.agent_summary import summarise
from analysis.manifest import RESULTS_DIR, Run


def rows_by_model(
    label: str,
) -> tuple[dict[tuple[str, str, str], dict[str, Any]], dict[tuple[str, str], set[str]]]:
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    sources: dict[tuple[str, str], set[str]] = {}
    for directory in sorted(RESULTS_DIR.glob("*-agent-run")):
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if manifest["config"].get("label") != label:
            continue
        for line in (directory / "agent_runs.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            model = " + ".join(row["models"]) or "no model call"
            rows[(model, row["scenario"], row["task_id"])] = row
            sources.setdefault((model, row["scenario"]), set()).add(directory.name)
    return rows, sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analysis.bakeoff")
    parser.add_argument("--label", default="bakeoff")
    args = parser.parse_args(argv)
    rows, sources = rows_by_model(args.label)
    groups = sorted({(model, scenario) for model, scenario, _ in rows})
    summary: dict[str, dict[str, Any]] = {}
    for model, scenario in groups:
        selected = [row for (m, s, _), row in sorted(rows.items()) if (m, s) == (model, scenario)]
        summary.setdefault(model, {})[scenario] = {
            **summarise(selected),
            "median_output_tokens": sorted(r["tokens_out"] for r in selected)[len(selected) // 2],
            "runs": sorted(sources[(model, scenario)]),
        }
    run = Run(kind="bakeoff", config={"label": args.label})
    run.write("bakeoff.json", summary)
    run.finish(models={"compared": ", ".join(summary)})
    for model, scenarios in summary.items():
        for scenario, figures in scenarios.items():
            print(
                f"{model:28} {scenario:14} {figures['passed']:2}/{figures['tasks']} passed, "
                f"{figures['wrong_actions']} wrong, {figures['claimed_success_but_failed']} false success, "
                f"median {figures['median_seconds']:.0f} s, {figures['median_llm_calls']} calls"
            )
    print(f"-> {run.directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
