"""Client for the portal's admin endpoints: chaos, reset, session expiry and the human side of the wall."""

from __future__ import annotations

import asyncio
import time
from types import TracebackType
from typing import Self

import httpx

from testbed.scenarios import ChaosConfig
from testbed.settings import Settings


class PortalAdmin:
    def __init__(self, settings: Settings) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.portal_base_url,
            headers={"X-Admin-Token": settings.portal_admin_token},
            timeout=60.0,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self._client.aclose()

    async def wait_ready(self, timeout_s: float = 120.0) -> None:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                if (await self._client.get("/internal/ui-config")).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError("portal did not become ready")
            await asyncio.sleep(1.0)

    async def apply(self, chaos: ChaosConfig) -> ChaosConfig:
        response = await self._client.put("/admin/chaos", json=chaos.to_json())
        response.raise_for_status()
        return ChaosConfig.model_validate(response.json())

    async def reset(self) -> None:
        (await self._client.post("/admin/reset")).raise_for_status()

    async def expire_sessions(self) -> None:
        (await self._client.post("/admin/sessions/expire")).raise_for_status()

    async def clear_wall(self, username: str) -> None:
        (await self._client.post("/admin/wall/clear", json={"username": username})).raise_for_status()
