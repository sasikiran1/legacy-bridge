"""Cost ledger (ADR 0007) for the committed agent runs and replay evaluations.

    uv run python -m analysis.cost_ledger [--labels m3,heal] [--price-model openai/gpt-oss-120b]

Local (Ollama) calls cost $0 notional; their model time is reported beside the dollars, never converted. To
make the tiers comparable in money, each group also carries a counterfactual: the same input and output tokens
priced at the price model's paid list price in effect on the price date (the ledger's run date), from
config/models.yaml. Replay evaluations made no model calls. Results hold counts and prices only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from typing import Any

import yaml

from analysis.manifest import RESULTS_DIR, Run
from proxy.providers import MODELS_YAML


def price_on(model: str, day: dt.date) -> dict[str, Any]:
    models = yaml.safe_load(MODELS_YAML.read_text(encoding="utf-8"))["models"]
    entry = next(m for m in models if m["id"] == model)
    for price in entry["prices"]:
        start = dt.date.fromisoformat(str(price["effective_from"]))
        end = dt.date.fromisoformat(str(price["effective_to"])) if price.get("effective_to") else None
        if start <= day and (end is None or day <= end):
            return {k: v.isoformat() if isinstance(v, dt.date) else v for k, v in price.items()}
    raise LookupError(f"no {model} price in effect on {day}")


def agent_rows(labels: list[str]) -> list[dict[str, Any]]:
    """The latest row per (label, scenario, task): a re-recorded task replaces its earlier runs."""
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for directory in sorted(RESULTS_DIR.glob("*-agent-run")):
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        label = manifest["config"].get("label")
        if label not in labels:
            continue
        for line in (directory / "agent_runs.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            rows[(label, row["scenario"], row["task_id"])] = {**row, "label": label, "run": directory.name}
    return list(rows.values())


def path(row: dict[str, Any]) -> str:
    """The tiers that ran, e.g. api>replay or api>replay>explore."""
    return ">".join(row["tiers"]) or "none"


def summarise(rows: list[dict[str, Any]], price: dict[str, Any]) -> dict[str, Any]:
    tokens_in = sum(r["tokens_in"] for r in rows)
    tokens_out = sum(r["tokens_out"] for r in rows)
    timed = [r["llm_seconds"] for r in rows if r.get("llm_seconds")]
    dollars = (tokens_in * price["input"] + tokens_out * price["output"]) / 1_000_000
    return {
        "tasks": len(rows),
        "passed": sum(r["passed"] for r in rows),
        "llm_calls": sum(r["llm_calls"] for r in rows),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "notional_usd": 0.0,
        "counterfactual_usd": round(dollars, 6),
        "counterfactual_usd_per_task": round(dollars / len(rows), 6) if rows else None,
        "median_seconds": statistics.median(r["seconds"] for r in rows),
        "model_seconds_total": round(sum(timed), 1) if timed else None,
    }


def replay_rows() -> list[dict[str, Any]]:
    rows = []
    for directory in sorted(RESULTS_DIR.glob("*-replay-eval")):
        data = json.loads((directory / "replay_eval.json").read_text(encoding="utf-8"))
        rows += [{**r, "run": directory.name} for r in data["runs"]]
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analysis.cost_ledger")
    parser.add_argument("--labels", default="m3,heal,trim,trim-control")
    parser.add_argument("--price-model", default="openai/gpt-oss-120b")
    args = parser.parse_args(argv)
    labels = args.labels.split(",")
    run = Run(kind="cost-ledger", config={"labels": labels, "price_model": args.price_model})
    price_date = dt.datetime.now(dt.UTC).date()
    price = price_on(args.price_model, price_date)
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in agent_rows(labels):
        groups.setdefault(f"{row['label']} / {row['scenario']} / {path(row)}", []).append(row)
    ledger = {key: summarise(rows, price) for key, rows in sorted(groups.items())}
    replays = replay_rows()
    for scenario in sorted({r["scenario"] for r in replays}):
        selected = [r for r in replays if r["scenario"] == scenario]
        ledger[f"replay-eval / {scenario} / replay"] = {
            "tasks": len(selected),
            "passed": sum(r["passed"] for r in selected),
            "llm_calls": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "notional_usd": 0.0,
            "counterfactual_usd": 0.0,
            "counterfactual_usd_per_task": 0.0,
            "median_seconds": statistics.median(r["seconds"] for r in selected),
            "model_seconds_total": 0.0,
        }
    run.write(
        "cost_ledger.json",
        {
            "price_model": args.price_model,
            "price_date": price_date.isoformat(),
            "price": price,
            "note": "Runs used a local model ($0 notional). counterfactual_usd prices the same tokens at the "
            "price model's list price on the price date.",
            "groups": ledger,
        },
    )
    run.finish(models={"price_model": args.price_model, "price_date": price_date.isoformat()})
    for key, figures in ledger.items():
        print(
            f"{key:52} {figures['passed']:2}/{figures['tasks']:<2} calls {figures['llm_calls']:3} "
            f"${figures['counterfactual_usd_per_task']:.5f}/task  median {figures['median_seconds']:.0f} s"
        )
    print(f"price {args.price_model} on {price_date}: {price['input']}/{price['output']} USD per 1M tokens")
    print(f"-> {run.directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
