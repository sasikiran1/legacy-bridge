"""Runtime settings from the environment, falling back to the repo's .env (which stays out of git)."""

from __future__ import annotations

import os
from functools import cache
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


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    portal_base_url: str
    portal_admin_token: str
    portal_test_user_password: str
    portal_db_url: str


@cache
def settings() -> Settings:
    file = read_dotenv(ENV_FILE)

    def get(key: str) -> str:
        value = os.environ.get(key) or file.get(key)
        if not value:
            raise RuntimeError(f"{key} is not set (environment or .env)")
        return value

    return Settings(
        portal_base_url=get("PORTAL_BASE_URL").rstrip("/"),
        portal_admin_token=get("PORTAL_ADMIN_TOKEN"),
        portal_test_user_password=get("PORTAL_TEST_USER_PASSWORD"),
        portal_db_url=get("PORTAL_DB_URL"),
    )
