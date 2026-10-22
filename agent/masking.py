"""Client for the proxy's /v1/mask: everything entering agent state is tokenized first."""

from __future__ import annotations

from typing import Any

import httpx


class MaskingUnavailable(Exception):
    """The proxy could not mask; the executor must not hand the raw result to the agent."""


class MaskClient:
    def __init__(self, proxy_url: str, http: httpx.AsyncClient | None = None) -> None:
        self._url = f"{proxy_url}/v1/mask"
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def text(self, task_id: str, content: str) -> str:
        return str(await self._post(task_id, {"content": content}))

    async def value(self, task_id: str, value: Any) -> Any:
        return await self._post(task_id, {"value": value})

    async def _post(self, task_id: str, payload: dict[str, Any]) -> Any:
        try:
            response = await self._http.post(self._url, json=payload, headers={"X-LB-Task-Id": task_id})
        except httpx.HTTPError as e:
            raise MaskingUnavailable(str(e)) from e
        if response.status_code != 200:
            raise MaskingUnavailable(f"{response.status_code}: {response.text[:200]}")
        return response.json()["masked"]
