"""Executors: the only code that touches the portal. They restore real values at the moment of acting
(under the
rehydration policy) and tokenize every result before it reaches agent state (ADR 0003)."""

from __future__ import annotations

import asyncio
import glob
import json
import re
from enum import StrEnum
from typing import Any
from urllib.parse import quote

import httpx
from mcp import Client, StdioServerParameters
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict

from agent.breaker import BreakerRegistry
from agent.masking import MaskClient
from agent.openapi import Operation
from proxy.rehydrate import RehydrationDenied, ToolArgumentResolver


class Failure(StrEnum):
    NOT_FOUND = "not_found"
    GONE = "gone"
    NOT_IMPLEMENTED = "not_implemented"
    SERVER_ERROR = "server_error"
    SCHEMA_DRIFT = "schema_drift"
    AUTH = "auth"
    CIRCUIT_OPEN = "circuit_open"
    CLIENT_ERROR = "client_error"
    POLICY_DENIED = "policy_denied"
    UNKNOWN_TOOL = "unknown_tool"


FALLBACK = frozenset(
    {
        Failure.NOT_FOUND,
        Failure.GONE,
        Failure.NOT_IMPLEMENTED,
        Failure.SERVER_ERROR,
        Failure.SCHEMA_DRIFT,
        Failure.AUTH,
        Failure.CIRCUIT_OPEN,
    }
)
"""Failures that mean the API cannot do this job right now. Client errors are the model's to fix."""


class ApiResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    operation: str
    status: int
    failure: Failure | None
    body: Any
    detail: str
    deprecated: bool = False

    @property
    def ok(self) -> bool:
        return self.failure is None

    def for_model(self) -> str:
        if self.ok:
            prefix = "[deprecated endpoint] " if self.deprecated else ""
            note = f" ({self.detail})" if self.detail else ""
            body = json.dumps(self.body, ensure_ascii=False) if not isinstance(self.body, str) else self.body
            return f"{prefix}HTTP {self.status}: {body}{note}"
        return f"HTTP {self.status} ({self.failure}): {self.detail}"


class ApiExecutor:
    def __init__(
        self,
        portal_url: str,
        portal_host: str,
        operations: dict[str, Operation],
        breakers: BreakerRegistry,
        resolver: ToolArgumentResolver,
        masker: MaskClient,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = portal_url  # OpenAPI paths already carry /api/v1
        self._host = portal_host
        self.operations = operations
        self.breakers = breakers
        self._resolver = resolver
        self._masker = masker
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(60.0))

    async def aclose(self) -> None:
        await self._http.aclose()

    def available(self) -> list[Operation]:
        return [op for op in self.operations.values() if self.breakers.get(op.id).allow()]

    async def call(
        self, task_id: str, credentials: tuple[str, str], operation_id: str, arguments: dict[str, Any]
    ) -> ApiResult:
        op = self.operations.get(operation_id)
        if op is None:
            return ApiResult(
                operation=operation_id,
                status=0,
                failure=Failure.UNKNOWN_TOOL,
                body=None,
                detail=f"no operation {operation_id}",
            )
        breaker = self.breakers.get(op.id)
        if not breaker.allow():
            return ApiResult(
                operation=op.id,
                status=0,
                failure=Failure.CIRCUIT_OPEN,
                body=None,
                detail="circuit open after repeated failures",
            )
        known = {p.name for p in op.parameters} | set(op.body_fields)
        ignored = sorted(set(arguments) - known)
        arguments = {k: v for k, v in arguments.items() if k in known and v not in (None, "")}
        try:
            resolved = await self._resolver.resolve(task_id, op.id, arguments, target_host=self._host)
        except RehydrationDenied as e:
            return ApiResult(
                operation=op.id, status=0, failure=Failure.POLICY_DENIED, body=None, detail=str(e)
            )

        path = op.path
        query = {}
        for p in op.parameters:
            if p.name not in resolved:
                continue
            if p.location == "path":
                path = path.replace("{" + p.name + "}", quote(str(resolved[p.name]), safe=""))
            elif p.location == "query":
                query[p.name] = str(resolved[p.name])
        body = {f: resolved[f] for f in op.body_fields if f in resolved and resolved[f] is not None}
        try:
            response = await self._http.request(
                op.method,
                f"{self._base}{path}",
                params=query,
                json=body if op.body_fields else None,
                auth=credentials,
            )
        except httpx.HTTPError as e:
            breaker.record(False)
            return ApiResult(
                operation=op.id, status=0, failure=Failure.SERVER_ERROR, body=None, detail=str(e)
            )

        failure, payload = self._classify(op, response)
        breaker.record(failure not in FALLBACK)
        masked_body = await self._masker.value(task_id, payload) if payload is not None else None
        detail = "" if failure is None else str(await self._masker.text(task_id, response.text[:500]))
        if ignored:
            detail = f"{detail} (ignored undocumented arguments: {', '.join(ignored)})".strip()
        return ApiResult(
            operation=op.id,
            status=response.status_code,
            failure=failure,
            body=masked_body if failure is None else None,
            detail=detail,
            deprecated="Deprecation" in response.headers,
        )

    @staticmethod
    def _classify(op: Operation, response: httpx.Response) -> tuple[Failure | None, Any]:
        status = response.status_code
        try:
            payload = response.json() if response.content else None
        except ValueError:
            payload = None
        message = str(payload.get("error", "")) if isinstance(payload, dict) else ""
        if status in (200, 201, 204):
            if status != 204 and op.check_response(payload):
                return Failure.SCHEMA_DRIFT, None
            return None, payload
        if status == 400 and "Unrecognized field" in message:
            return Failure.SCHEMA_DRIFT, None
        if status in (401, 403):
            return Failure.AUTH, None
        if status == 404:
            # A removed route answers a bare "Not Found"; a missing record names the record.
            return (Failure.NOT_FOUND if message in ("", "Not Found") else Failure.CLIENT_ERROR), None
        if status == 410:
            return Failure.GONE, None
        if status == 501:
            return Failure.NOT_IMPLEMENTED, None
        if status >= 500:
            return Failure.SERVER_ERROR, None
        return Failure.CLIENT_ERROR, None


# --- browser


BROWSER_TOOLS = (
    "browser_navigate",
    "browser_navigate_back",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_select_option",
    "browser_press_key",
    "browser_wait_for",
    "browser_handle_dialog",
)
"""The MCP tools the model may use. Never browser_evaluate or browser_run_code_unsafe."""

_ELEMENT = re.compile(
    r'^\s*- (?P<role>[a-z]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?[^\n]*?\[ref=(?P<ref>(?:f\d+)?e\d+)\]', re.M
)
_SNAPSHOT = re.compile(r"### Snapshot\s*```yaml\n(?P<tree>.*?)```", re.S)
_PAGE = re.compile(r"### Page\n(?P<page>.*?)(?=\n###|\Z)", re.S)
_REF = re.compile(r"(?:f\d+)?e\d+")
"""Playwright MCP element refs: e12 on the first snapshot, f3e12 after later navigations."""

_SETTLE_CODE = (
    "async (page) => { await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {}); }"
)

_LOGIN_CODE = """async (page) => {
  const password = page.locator('input[type=password]').first();
  const user = page.locator('input:not([type=password]):not([type=hidden])').first();
  await user.fill(%(user)s);
  await password.fill(%(password)s);
  await password.press('Enter');
  await page.waitForLoadState('networkidle');
}"""


def element_index(snapshot: str) -> dict[str, tuple[str, str]]:
    """ref -> (role, accessible name) from a Playwright MCP accessibility snapshot."""
    return {m["ref"]: (m["role"], (m["name"] or "").replace('\\"', '"')) for m in _ELEMENT.finditer(snapshot)}


def page_state(text: str) -> str:
    """The page header and accessibility tree from an MCP response, without the Playwright code it ran."""
    page = _PAGE.search(text)
    tree = _SNAPSHOT.search(text)
    parts = []
    if page:
        parts.append(page.group("page").strip())
    if tree:
        parts.append(tree.group("tree").rstrip())
    return "\n".join(parts) if parts else text.strip()


class BrowserObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    error: str | None = None
    raw: str = ""


class BrowserSession:
    """One headless Chromium driven over Playwright MCP, confined to the portal origin.

    The MCP client lives in a dedicated owner task and callers send it work over a queue: LangGraph runs each
    node in its own asyncio task, and anyio forbids entering a client context in one task and leaving it in
    another.
    """

    def __init__(
        self, portal_origin: str, portal_host: str, resolver: ToolArgumentResolver, masker: MaskClient
    ) -> None:
        self._origin = portal_origin
        self._host = portal_host
        self._resolver = resolver
        self._masker = masker
        self._queue: asyncio.Queue[tuple[str, dict[str, Any], asyncio.Future[CallToolResult]] | None] = (
            asyncio.Queue()
        )
        self._owner: asyncio.Task[None] | None = None
        self.last_snapshot = ""

    def _parameters(self) -> StdioServerParameters:
        chrome = sorted(glob.glob("/ms-playwright/chromium-*/chrome-linux*/chrome"))
        args = [
            "--headless",
            "--isolated",
            "--no-sandbox",
            "--allowed-origins",
            self._origin,
            "--image-responses",
            "omit",
            "--viewport-size",
            "1280x900",
            # Refs are numbered when an element is first snapshotted. Action responses carry a snapshot taken
            # 500 ms after the action, before the page's data may have loaded, so refs depended on timing and
            # a replay could click a different element. Refs are assigned only by `snapshot()` after settling.
            "--snapshot-mode",
            "none",
        ]
        if chrome:
            args += ["--executable-path", chrome[-1]]
        return StdioServerParameters(command="playwright-mcp", args=args)

    async def start(self) -> BrowserSession:
        ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._owner = asyncio.create_task(self._serve(ready))
        await ready
        return self

    async def _serve(self, ready: asyncio.Future[None]) -> None:
        try:
            async with Client(self._parameters()) as client:
                ready.set_result(None)
                while (item := await self._queue.get()) is not None:
                    tool, arguments, result = item
                    try:
                        result.set_result(await client.call_tool(tool, arguments))
                    except Exception as e:
                        result.set_exception(e)
        except Exception as e:
            if not ready.done():
                ready.set_exception(e)
            raise

    async def close(self) -> None:
        if self._owner is not None:
            await self._queue.put(None)
            await self._owner
            self._owner = None

    async def _call(self, tool: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        if self._owner is None or self._owner.done():
            raise RuntimeError("browser session is not running")
        future: asyncio.Future[CallToolResult] = asyncio.get_running_loop().create_future()
        await self._queue.put((tool, arguments, future))
        result = await future
        text = "\n".join(b.text for b in result.content if isinstance(b, TextContent))
        return text, bool(result.is_error)

    async def _settle(self) -> None:
        """Waits for the SPA's data requests to finish, so snapshots show content rather than spinners."""
        await self._call("browser_run_code_unsafe", {"code": _SETTLE_CODE})

    async def snapshot(self) -> str:
        text, _ = await self._call("browser_snapshot", {})
        self.last_snapshot = page_state(text)
        return self.last_snapshot

    async def login(self, username: str, password: str) -> str:
        """Signs in with executor-only code; credentials never appear in agent state or any prompt."""
        await self._call("browser_navigate", {"url": f"{self._origin}/login"})
        code = _LOGIN_CODE % {"user": _js(username), "password": _js(password)}
        await self._call("browser_run_code_unsafe", {"code": code})
        return await self.snapshot()

    def describe(self, ref: str) -> tuple[str, str] | None:
        return element_index(self.last_snapshot).get(ref)

    async def act(self, task_id: str, tool: str, arguments: dict[str, Any]) -> BrowserObservation:
        if tool not in BROWSER_TOOLS:
            return BrowserObservation(text="", error=f"{tool} is not available")
        target = arguments.get("target")
        if target is not None and not _REF.fullmatch(str(target)):
            # Selectors would bypass the label-based data policy and the destructive-click approval check.
            return BrowserObservation(
                text="", error="target must be a ref from the latest snapshot, e.g. e12"
            )
        try:
            arguments = await self._resolve(task_id, tool, dict(arguments))
        except RehydrationDenied as e:
            return BrowserObservation(text="", error=f"refused by the data policy: {e}")
        if tool != "browser_snapshot":
            text, is_error = await self._call(tool, arguments)
            if is_error:
                error = await self._masker.text(task_id, page_state(text)[:800])
                return BrowserObservation(text="", error=error)
            await self._settle()
        raw = await self.snapshot()
        return BrowserObservation(text=await self._masker.text(task_id, raw), raw=raw)

    async def _resolve(self, task_id: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool == "browser_navigate":
            url = str(arguments.get("url", ""))
            if url.startswith("/"):
                url = self._origin + url
            if not url.startswith(self._origin + "/") and url != self._origin:
                raise RehydrationDenied(f"{url} is outside the portal")
            resolved = await self._resolver.resolve(
                task_id, "browser_navigate", {"url": url}, target_host=self._host
            )
            arguments["url"] = resolved["url"]
        if tool in ("browser_type", "browser_select_option"):
            element = self.describe(str(arguments.get("target", "")))
            label = element[1] if element else None
            if "text" in arguments:
                arguments["text"] = await self._resolver.resolve_field(
                    task_id, tool, label, str(arguments["text"])
                )
            if "values" in arguments and isinstance(arguments["values"], list):
                arguments["values"] = [
                    await self._resolver.resolve_field(task_id, tool, label, str(v))
                    for v in arguments["values"]
                ]
        return arguments


def _js(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
