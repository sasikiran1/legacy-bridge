"""Agent settings from the environment (or the repo's .env)."""

from __future__ import annotations

import base64
from enum import StrEnum
from functools import cache, partial
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from testbed.settings import ENV_FILE, environment_value, read_dotenv


class Profile(StrEnum):
    """Which models each node asks for. `local` sends every role to the local model (the bulk-experiment
    arm)."""

    LOCAL = "local"
    HOSTED = "hosted"


ROLES = {
    Profile.LOCAL: {"plan": "local_text", "api": "local_text", "explore": "local_text"},
    Profile.HOSTED: {"plan": "plan", "api": "api_tier", "explore": "explore"},
}


class AgentSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    proxy_url: str
    portal_url: str
    redis_url: str
    vault_master_key: bytes
    vault_task_ttl_seconds: int
    agent_db_url: str
    test_user_password: str
    profile: Profile
    injection_scanner: str
    snapshots: str = "raw"
    """raw, or trimmed (agent.executors.trim_snapshot): what the browser tier shows the model."""
    max_api_steps: int = 8
    max_explore_steps: int = 24

    @property
    def portal_host(self) -> str:
        return urlparse(self.portal_url).hostname or ""

    @property
    def portal_origin(self) -> str:
        parsed = urlparse(self.portal_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def role(self, node: str) -> str:
        return ROLES[self.profile][node]


@cache
def agent_settings() -> AgentSettings:
    get = partial(environment_value, read_dotenv(ENV_FILE))

    return AgentSettings(
        proxy_url=get("PROXY_URL", "http://localhost:8090").rstrip("/"),
        portal_url=get("PORTAL_BASE_URL").rstrip("/"),
        redis_url=get("REDIS_URL"),
        vault_master_key=base64.b64decode(get("VAULT_MASTER_KEY")),
        vault_task_ttl_seconds=int(get("VAULT_TASK_TTL_SECONDS", "3600")),
        agent_db_url=get("AGENT_DB_URL"),
        test_user_password=get("PORTAL_TEST_USER_PASSWORD"),
        profile=Profile(get("AGENT_PROFILE", Profile.LOCAL.value)),
        injection_scanner=get("INJECTION_SCANNER", "none"),
        snapshots=get("AGENT_SNAPSHOTS", "raw"),
    )
