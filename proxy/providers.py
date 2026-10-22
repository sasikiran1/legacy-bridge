"""Upstream providers and the model→provider map, from config/models.yaml."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

MODELS_YAML = Path(__file__).resolve().parent.parent / "config" / "models.yaml"


class Provider(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    base_url: str
    api_key: str | None
    trust: str


class Registry:
    def __init__(self, providers: dict[str, Provider], models: dict[str, str]) -> None:
        self._providers = providers
        self._models = models

    def provider_for(self, model: str) -> Provider | None:
        name = self._models.get(model)
        return self._providers.get(name) if name else None

    @property
    def models(self) -> list[str]:
        return sorted(self._models)


def load_registry(path: Path = MODELS_YAML) -> Registry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    providers = {}
    for name, spec in raw["providers"].items():
        base_url = spec.get("base_url") or os.environ.get(spec.get("base_url_env", ""), "")
        if not base_url:
            continue
        key_env = spec.get("api_key_env")
        providers[name] = Provider(
            name=name,
            base_url=base_url.rstrip("/"),
            api_key=os.environ.get(key_env) if key_env else None,
            trust=spec["trust"],
        )
    models = {m["id"]: m["provider"] for m in raw["models"] if m["provider"] in providers}
    return Registry(providers, models)
