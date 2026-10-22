"""Chaos configuration (mirrors the portal ChaosConfig record) and the scenarios in config/scenarios.yaml."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

SCENARIOS_YAML = Path(__file__).resolve().parent.parent / "config" / "scenarios.yaml"

Operation = Literal[
    "searchMembers",
    "getMember",
    "updateAddress",
    "updateContact",
    "listPolicies",
    "getPolicy",
    "listClaims",
    "getClaim",
    "requestReconsideration",
    "submitReimbursement",
    "listNotes",
    "addNote",
    "deleteNote",
]


class ApiMode(StrEnum):
    OK = "OK"
    DEPRECATED = "DEPRECATED"
    DRIFT = "DRIFT"
    NOT_FOUND = "NOT_FOUND"
    GONE = "GONE"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    SERVER_ERROR = "SERVER_ERROR"


class Wall(StrEnum):
    NONE = "NONE"
    CAPTCHA = "CAPTCHA"
    MFA = "MFA"


class _Camel(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", alias_generator=to_camel, validate_by_name=True, validate_by_alias=True
    )


class DomDrift(_Camel):
    labels: bool = False
    layout: bool = False
    interstitial: bool = False


class ChaosConfig(_Camel):
    api: dict[Operation, ApiMode] = {}
    dom_drift: DomDrift = DomDrift()
    latency_ms: int = 0
    session_ttl_seconds: int = 1800
    wall: Wall = Wall.NONE
    poor_a11y: bool = Field(default=False, alias="poorA11y")
    injected_notes: bool = False

    def to_json(self) -> dict[str, object]:
        return self.model_dump(mode="json", by_alias=True)


class Scenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    chaos: ChaosConfig

    @property
    def ui_blocked(self) -> bool:
        """Sign-in to the SPA is impossible without a human."""
        return self.chaos.wall is not Wall.NONE


def load_scenarios(path: Path = SCENARIOS_YAML) -> dict[str, Scenario]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))["scenarios"]
    return {name: Scenario(name=name, **body) for name, body in raw.items()}
