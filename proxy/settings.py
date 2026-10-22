"""Proxy settings from the environment (or the repo's .env)."""

from __future__ import annotations

import base64
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from proxy.detection import DetectorConfig
from testbed.settings import ENV_FILE, read_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent


class ProxySettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    redis_url: str
    vault_master_key: bytes
    vault_task_ttl_seconds: int
    token_key: bytes
    run_seed: str
    audit_path: Path
    detector: DetectorConfig
    allowed_hosts: frozenset[str]


def _key(name: str, raw: str) -> bytes:
    try:
        key = base64.b64decode(raw, validate=True)
    except ValueError as e:
        raise RuntimeError(f"{name} must be base64") from e
    if len(key) != 32:
        raise RuntimeError(f"{name} must decode to 32 bytes")
    return key


def load_settings() -> ProxySettings:
    file = read_dotenv(ENV_FILE)

    def get(name: str, default: str | None = None) -> str:
        value = os.environ.get(name) or file.get(name) or default
        if not value:
            raise RuntimeError(f"{name} is not set (environment or .env)")
        return value

    return ProxySettings(
        redis_url=get("REDIS_URL"),
        vault_master_key=_key("VAULT_MASTER_KEY", get("VAULT_MASTER_KEY")),
        vault_task_ttl_seconds=int(get("VAULT_TASK_TTL_SECONDS", "3600")),
        token_key=_key("TOKEN_KEY", get("TOKEN_KEY")),
        run_seed=get("RUN_SEED", "7"),
        audit_path=Path(get("AUDIT_LOG_PATH", str(REPO_ROOT / ".cache" / "audit" / "proxy.jsonl"))),
        detector=DetectorConfig(get("PII_DETECTOR", DetectorConfig.FULL.value)),
        allowed_hosts=frozenset(get("ALLOWED_HOSTS", "localhost,portal").split(",")),
    )
