from __future__ import annotations

import pytest
from pydantic import ValidationError

from testbed.scenarios import ApiMode, ChaosConfig, Wall, load_scenarios

EXPECTED = {
    "healthy",
    "api_degraded",
    "dom_drift",
    "api_degraded_dom_drift",
    "poor_a11y",
    "captcha",
    "injected_notes",
}


def test_all_spec_scenarios_are_defined() -> None:
    assert set(load_scenarios()) == EXPECTED


def test_only_the_captcha_scenario_blocks_the_ui() -> None:
    blocked = {name for name, s in load_scenarios().items() if s.ui_blocked}
    assert blocked == {"captcha"}


def test_chaos_serializes_with_the_portal_field_names() -> None:
    config = load_scenarios()["api_degraded_dom_drift"].chaos
    body = config.to_json()
    assert body["domDrift"] == {"labels": True, "layout": True, "interstitial": True}
    api = body["api"]
    assert isinstance(api, dict)
    assert api["updateAddress"] == ApiMode.NOT_FOUND
    assert {"latencyMs", "sessionTtlSeconds", "poorA11y", "injectedNotes", "wall"} <= body.keys()
    assert ChaosConfig.model_validate(body) == config


def test_unknown_operations_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ChaosConfig.model_validate({"api": {"getMembr": "GONE"}})


def test_wall_defaults_to_none() -> None:
    assert ChaosConfig().wall is Wall.NONE
