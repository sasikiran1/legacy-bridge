"""The PII policy in config/pii-policy.yaml."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

POLICY_YAML = Path(__file__).resolve().parent.parent / "config" / "pii-policy.yaml"


class EntityRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    min_score: float
    rehydratable: bool = True


def normalize_key(key: str) -> str:
    """`member_number`, `memberNumber` and `MEMBER-NUMBER` all become `membernumber`."""
    return re.sub(r"[^a-z0-9]", "", key.lower())


class PiiPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    entities: dict[str, EntityRule]
    structured_fields: dict[str, list[str]]
    rehydration: dict[str, dict[str, list[str]]]
    browser_fields: dict[str, list[str]] = {}

    @model_validator(mode="after")
    def _types_are_declared(self) -> PiiPolicy:
        declared = set(self.entities)
        referenced = (
            set(self.structured_fields)
            | set(self.browser_fields)
            | {entity for args in self.rehydration.values() for types in args.values() for entity in types}
        )
        unknown = referenced - declared
        if unknown:
            raise ValueError(f"policy references undeclared entity types: {sorted(unknown)}")
        for tool, args in self.rehydration.items():
            for argument, types in args.items():
                blocked = [t for t in types if not self.entities[t].rehydratable]
                if blocked:
                    raise ValueError(f"{tool}.{argument} allows non-rehydratable types {blocked}")
        return self

    def tokenizes(self, entity_type: str) -> bool:
        return entity_type in self.entities

    def min_score(self, entity_type: str) -> float:
        return self.entities[entity_type].min_score

    def field_type(self, key: str) -> str | None:
        normalized = normalize_key(key)
        for entity, keys in self.structured_fields.items():
            if normalized in {normalize_key(k) for k in keys}:
                return entity
        return None

    def may_rehydrate(self, tool: str, argument: str, entity_type: str) -> bool:
        return entity_type in self.rehydration.get(tool, {}).get(argument, [])

    def may_type_into(self, entity_type: str, label: str | None) -> bool:
        """Whether a real value of this type may be typed into a UI field with this accessible name."""
        if not label:
            return False
        lowered = label.lower()
        return any(pattern in lowered for pattern in self.browser_fields.get(entity_type, []))


def load_policy(path: Path = POLICY_YAML) -> PiiPolicy:
    return PiiPolicy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
