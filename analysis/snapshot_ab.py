"""Live A/B of page snapshots, trimmed vs. raw (AGENT_SNAPSHOTS), from the committed agent runs.

    uv run python -m analysis.snapshot_ab [--trimmed trim] [--raw trim-control]

Each arm is the latest row per (scenario, task) from the runs with that label. Success is compared on every
task; prompt tokens only on tasks that reached the browser in both arms, since an API-only task never sends a
snapshot. Per call, because the two arms may take different numbers of steps. Results hold counts only.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from typing import Any

from analysis.manifest import RESULTS_DIR, Run, agent_run_manifests, agent_run_rows


def arm(label: str) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    manifests: dict[str, Any] = {}
    for directory, manifest in agent_run_manifests(RESULTS_DIR, [label]):
        manifests[directory.name] = {
            "snapshots": manifest["config"].get("snapshots", "raw"),
            "runtime": manifest["models"].get("runtime"),
            "served": manifest["models"].get("served"),
        }
        for row in agent_run_rows(directory):
            rows[(row["scenario"], row["task_id"])] = row
    return rows, manifests


def per_call(rows: list[dict[str, Any]]) -> float:
    tokens: int = sum(r["tokens_in"] for r in rows)
    calls: int = sum(r["llm_calls"] for r in rows)
    return round(tokens / calls, 1)


def compare(trimmed: list[dict[str, Any]], raw: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = zip(trimmed, raw, strict=True)
    browser = [(t, r) for t, r in pairs if "explore" in t["tiers"] and "explore" in r["tiers"]]
    figures: dict[str, Any] = {
        "tasks": len(trimmed),
        "passed": {"trimmed": sum(r["passed"] for r in trimmed), "raw": sum(r["passed"] for r in raw)},
        "wrong_actions": {
            "trimmed": sum(r["wrong_action"] for r in trimmed),
            "raw": sum(r["wrong_action"] for r in raw),
        },
        "failed_tasks": {
            "trimmed": [r["task_id"] for r in trimmed if not r["passed"]],
            "raw": [r["task_id"] for r in raw if not r["passed"]],
        },
        "browser_tasks_in_both": len(browser),
    }
    if browser:
        t_rows, r_rows = [t for t, _ in browser], [r for _, r in browser]
        figures |= {
            "prompt_tokens_per_call": {"trimmed": per_call(t_rows), "raw": per_call(r_rows)},
            "reduction_per_call": round(1 - per_call(t_rows) / per_call(r_rows), 3),
            "median_llm_calls": {
                "trimmed": statistics.median(r["llm_calls"] for r in t_rows),
                "raw": statistics.median(r["llm_calls"] for r in r_rows),
            },
            "median_seconds": {
                "trimmed": statistics.median(r["seconds"] for r in t_rows),
                "raw": statistics.median(r["seconds"] for r in r_rows),
            },
        }
    return figures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analysis.snapshot_ab")
    parser.add_argument("--trimmed", default="trim")
    parser.add_argument("--raw", default="trim-control")
    args = parser.parse_args(argv)
    trimmed, trimmed_runs = arm(args.trimmed)
    raw, raw_runs = arm(args.raw)
    modes = {m["snapshots"] for m in trimmed_runs.values()}, {m["snapshots"] for m in raw_runs.values()}
    if modes != ({"trimmed"}, {"raw"}):
        raise SystemExit(f"arms are not trimmed vs. raw: {modes}")
    keys = sorted(trimmed.keys() & raw.keys())
    results: dict[str, Any] = {}
    for scenario in sorted({s for s, _ in keys}):
        selected = [k for k in keys if k[0] == scenario]
        results[scenario] = compare([trimmed[k] for k in selected], [raw[k] for k in selected])
    results["all"] = compare([trimmed[k] for k in keys], [raw[k] for k in keys])
    run = Run(kind="snapshot-ab", config={"trimmed": args.trimmed, "raw": args.raw})
    run.write("snapshot_ab.json", {"runs": {"trimmed": trimmed_runs, "raw": raw_runs}, "scenarios": results})
    run.finish(
        models={
            "served": ", ".join(sorted({m["served"] for m in (trimmed_runs | raw_runs).values()})),
            "runtime": ", ".join(sorted({m["runtime"] for m in (trimmed_runs | raw_runs).values()})),
        }
    )
    for scenario, f in results.items():
        tokens = f.get("prompt_tokens_per_call", {})
        print(
            f"{scenario:14} passed {f['passed']['trimmed']}/{f['tasks']} trimmed vs "
            f"{f['passed']['raw']}/{f['tasks']} raw; "
            f"tokens/call {tokens.get('trimmed')} vs {tokens.get('raw')} ({f.get('reduction_per_call')})"
        )
    print(f"-> {run.directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
