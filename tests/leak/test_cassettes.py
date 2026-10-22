"""Recorded LLM exchanges and run results are committed, so they must never contain a canary value."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from proxy.cassettes import CASSETTE_DIR
from testbed.seed import load_canaries

RESULTS_DIR = Path(__file__).resolve().parents[2] / "analysis" / "results"


def readable(path: Path) -> str:
    """JSON is decoded and re-encoded without escapes, so a `\\u` escape cannot hide a value."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.dumps(json.loads(text), ensure_ascii=False)
    if path.suffix == ".jsonl":
        return "\n".join(
            json.dumps(json.loads(line), ensure_ascii=False) for line in text.splitlines() if line
        )
    return text


def leaks(root: Path, patterns: tuple[str, ...]) -> list[tuple[str, str]]:
    canaries = load_canaries()
    found: list[tuple[str, str]] = []
    for pattern in patterns:
        for path in sorted(root.rglob(pattern)):
            text = readable(path)
            found += [(str(path.relative_to(root)), str(c.entity_type)) for c in canaries if c.value in text]
    return found


@pytest.mark.parametrize(
    ("root", "patterns"),
    [(CASSETTE_DIR, ("*.json",)), (RESULTS_DIR, ("*.json", "*.jsonl", "*.csv", "*.md"))],
    ids=["cassettes", "results"],
)
def test_no_committed_file_contains_a_canary_value(root: Path, patterns: tuple[str, ...]) -> None:
    assert leaks(root, patterns) == []
