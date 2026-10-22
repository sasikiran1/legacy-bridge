"""Recorded LLM exchanges are committed, so they must never contain a canary value."""

from __future__ import annotations

import json

from proxy.cassettes import CASSETTE_DIR
from testbed.seed import load_canaries


def test_no_cassette_contains_a_canary_value() -> None:
    canaries = load_canaries()
    leaks = []
    for path in sorted(CASSETTE_DIR.rglob("*.json")):
        text = json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False)
        leaks += [(path.name, c.entity_type) for c in canaries if c.value in text]
    assert leaks == []
