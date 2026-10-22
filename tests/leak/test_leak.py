"""CI gate: no canary value may reach a provider. Uses the production detector configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis.leak_check import check
from proxy.detection import DetectorConfig, build_detector
from proxy.policy import load_policy

pytestmark = pytest.mark.models


async def test_zero_canaries_leave_through_the_proxy(tmp_path: Path) -> None:
    report = await check(build_detector(DetectorConfig.FULL, load_policy()), tmp_path)
    assert report.requests == report.documents > 0
    assert report.canaries > 100
    assert report.leaked == (), [leak.model_dump() for leak in report.leaked]


async def test_detector_finds_pii_in_free_text() -> None:
    detector = build_detector(DetectorConfig.FULL, load_policy())
    text = "Please call Margaret Okonkwo at (415) 555-0132 or margaret.o@example.org about 12 Elm Street."
    found = {text[s.start : s.end]: s.entity_type for s in detector.detect(text)}
    assert found.get("Margaret Okonkwo") == "PERSON"
    assert found.get("(415) 555-0132") == "PHONE_NUMBER"
    assert found.get("margaret.o@example.org") == "EMAIL_ADDRESS"
    assert any(v == "STREET_ADDRESS" and "12 Elm Street" in k for k, v in found.items())
