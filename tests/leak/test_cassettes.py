"""Recorded LLM exchanges, screenshots, run results and replay scripts are committed: none may contain a
canary value."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from proxy.cassettes import CASSETTE_DIR
from proxy.vision import ocr_text
from testbed.seed import load_canaries

RESULTS_DIR = Path(__file__).resolve().parents[2] / "analysis" / "results"
REPLAY_DIR = Path(__file__).resolve().parents[2] / "agent" / "replays"


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
    [
        (CASSETTE_DIR, ("*.json",)),
        (RESULTS_DIR, ("*.json", "*.jsonl", "*.csv", "*.md")),
        (REPLAY_DIR, ("*.yaml",)),
    ],
    ids=["cassettes", "results", "replay scripts"],
)
def test_no_committed_file_contains_a_canary_value(root: Path, patterns: tuple[str, ...]) -> None:
    assert leaks(root, patterns) == []


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs tesseract-ocr")
async def test_no_recorded_screenshot_shows_a_canary_value() -> None:
    """Screenshots are stored as the provider received them (redacted); OCR must not read a canary on any."""
    canaries = load_canaries()
    found = []
    for root in (CASSETTE_DIR, RESULTS_DIR):
        for path in sorted(root.rglob("*.png")):
            text = await ocr_text(path.read_bytes())
            found += [(str(path.relative_to(root)), str(c.entity_type)) for c in canaries if c.value in text]
    assert found == []
