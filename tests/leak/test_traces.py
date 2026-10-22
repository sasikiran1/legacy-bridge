"""Traces stay counts and ids: no span stored in Phoenix may contain a canary value."""

from __future__ import annotations

import json
import os

import httpx
import pytest

from testbed.seed import load_canaries

pytestmark = pytest.mark.phoenix


def test_no_stored_span_contains_a_canary_value() -> None:
    url = os.environ.get("PHOENIX_URL", "http://phoenix:6006").rstrip("/")
    response = httpx.get(f"{url}/v1/projects/default/spans", params={"limit": 1000}, timeout=30)
    response.raise_for_status()
    spans = response.json()["data"]
    assert spans, "no spans: run the agent with OTEL_EXPORTER_OTLP_ENDPOINT set first"
    text = json.dumps(spans, ensure_ascii=False)
    assert [c.entity_type for c in load_canaries() if c.value in text] == []
