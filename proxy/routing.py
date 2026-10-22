"""Role → model fallback chains with client-side rate limits (config/models.yaml).

A request names either a concrete model id or a role (`plan`, `explore`, `local_text`, ...). The proxy tries
the role's chain in order, skipping models whose rate budget is spent and moving on after a 429 or 5xx.
Limits are the provider's published free-tier numbers; `null` means the number is unknown and not enforced.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from proxy.providers import MODELS_YAML


@dataclass(frozen=True)
class Limits:
    rpm: int | None = None
    tpm: int | None = None
    rpd: int | None = None
    tpd: int | None = None


@dataclass
class Budget:
    """Sliding one-minute window plus a calendar-day counter. Token use is estimated before the call."""

    limits: Limits
    minute: deque[tuple[float, int]] = field(default_factory=deque)
    day: str = ""
    day_requests: int = 0
    day_tokens: int = 0

    def _roll(self, now: float) -> None:
        while self.minute and now - self.minute[0][0] >= 60:
            self.minute.popleft()
        today = time.strftime("%Y-%m-%d", time.gmtime(now))
        if today != self.day:
            self.day, self.day_requests, self.day_tokens = today, 0, 0

    def wait_seconds(self, tokens: int, now: float | None = None) -> float | None:
        """Seconds until a call of this size fits; 0 if it fits now; None if today's budget is spent."""
        now = time.time() if now is None else now
        self._roll(now)
        lim = self.limits
        if (lim.rpd is not None and self.day_requests + 1 > lim.rpd) or (
            lim.tpd is not None and self.day_tokens + tokens > lim.tpd
        ):
            return None
        over_rpm = lim.rpm is not None and len(self.minute) + 1 > lim.rpm
        over_tpm = lim.tpm is not None and sum(t for _, t in self.minute) + tokens > lim.tpm
        if not (over_rpm or over_tpm):
            return 0.0
        return max(0.0, 60 - (now - self.minute[0][0])) if self.minute else 0.0

    def spend(self, tokens: int, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._roll(now)
        self.minute.append((now, tokens))
        self.day_requests += 1
        self.day_tokens += tokens


class Router:
    def __init__(
        self, roles: dict[str, list[str]], limits: dict[str, Limits], max_wait_seconds: float = 20.0
    ) -> None:
        self._roles = roles
        self._budgets = {model: Budget(lim) for model, lim in limits.items()}
        self._max_wait = max_wait_seconds
        self._lock = asyncio.Lock()

    def chain(self, requested: str) -> list[str]:
        return list(self._roles.get(requested, [requested]))

    async def acquire(self, model: str, tokens: int) -> bool:
        """Reserves budget for one call, waiting briefly if the minute is full. False: skip this model."""
        budget = self._budgets.get(model)
        if budget is None:
            return True
        async with self._lock:
            wait = budget.wait_seconds(tokens)
            if wait is None or wait > self._max_wait:
                return False
            if wait:
                await asyncio.sleep(wait)
            budget.spend(tokens)
            return True


def estimate_tokens(body: dict[str, object]) -> int:
    """Rough prompt+completion estimate (4 characters per token), used before the provider reports usage."""
    prompt = len(str(body.get("messages", ""))) + len(str(body.get("tools", "")))
    completion = body.get("max_tokens") or body.get("max_completion_tokens") or 1024
    return prompt // 4 + (completion if isinstance(completion, int) else 1024)


def load_router(path: Path = MODELS_YAML, local_text: str | None = None) -> Router:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    roles = {name: list(chain or []) for name, chain in raw.get("roles", {}).items()}
    if local_text:
        roles["local_text"] = [local_text]
    limits = {}
    for model in raw["models"]:
        free = model.get("free_limits") or {}
        limits[model["id"]] = Limits(
            rpm=free.get("rpm"), tpm=free.get("tpm"), rpd=free.get("rpd"), tpd=free.get("tpd")
        )
    return Router(roles, limits)
