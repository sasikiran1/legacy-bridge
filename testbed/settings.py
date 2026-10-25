"""Runtime settings from the environment, falling back to the repo's .env (which stays out of git)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import cache, partial
from pathlib import Path

from pydantic import BaseModel, ConfigDict

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def environment_value(file: Mapping[str, str], name: str, default: str | None = None) -> str:
    value = os.environ.get(name) or file.get(name) or default
    if not value:
        raise RuntimeError(f"{name} is not set (environment or .env)")
    return value


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    portal_base_url: str
    portal_admin_token: str
    portal_test_user_password: str
    portal_db_url: str


@cache
def settings() -> Settings:
    get = partial(environment_value, read_dotenv(ENV_FILE))

    return Settings(
        portal_base_url=get("PORTAL_BASE_URL").rstrip("/"),
        portal_admin_token=get("PORTAL_ADMIN_TOKEN"),
        portal_test_user_password=get("PORTAL_TEST_USER_PASSWORD"),
        portal_db_url=get("PORTAL_DB_URL"),
    )
