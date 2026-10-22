"""OpenAI-compatible chat completions proxy that tokenizes PII before anything leaves for a model.

    uv run uvicorn proxy.app:app --port 8090

Headers:
    X-LB-Task-Id      required; scopes tokens, the vault and the audit trail
    X-LB-Rehydrate    `final` restores real values in the returned text (operator-facing output only);
                      by default responses keep tokens, since the agent loop itself stays in token space

Fails closed: if detection or the vault is unavailable, the request is not forwarded (503).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from proxy.audit import AuditLog
from proxy.detection import DetectionError, build_detector
from proxy.masking import Masker
from proxy.policy import load_policy
from proxy.providers import Provider, Registry, load_registry
from proxy.rehydrate import Rehydrator, StreamRehydrator
from proxy.settings import load_settings
from proxy.tokens import TokenMinter
from proxy.vault import RedisVault, Sealer, TokenCollision, Vault, VaultUnavailable

TASK_HEADER = "x-lb-task-id"
REHYDRATE_HEADER = "x-lb-rehydrate"
CLIENT_DESTINATION = "client"


@dataclass
class Services:
    masker: Masker
    rehydrator: Rehydrator
    vault: Vault
    registry: Registry
    http: httpx.AsyncClient
    audit: AuditLog


class UnsupportedContent(Exception):
    pass


def _error(status: int, message: str, kind: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"message": message, "type": kind}})


async def mask_request(
    body: dict[str, Any], masker: Masker, task_id: str, destination: str
) -> dict[str, Any]:
    masked = dict(body)
    messages = []
    for message in body.get("messages", []):
        message = dict(message)
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = await masker.mask_content(content, task_id, destination)
        elif isinstance(content, list):
            parts = []
            for part in content:
                if part.get("type") != "text":
                    raise UnsupportedContent("non-text content needs screenshot redaction (ADR 0008)")
                parts.append({**part, "text": await masker.mask_content(part["text"], task_id, destination)})
            message["content"] = parts
        if message.get("tool_calls"):
            calls = []
            for call in message["tool_calls"]:
                function = dict(call.get("function", {}))
                if isinstance(function.get("arguments"), str):
                    function["arguments"] = await masker.mask_content(
                        function["arguments"], task_id, destination
                    )
                calls.append({**call, "function": function})
            message["tool_calls"] = calls
        messages.append(message)
    masked["messages"] = messages
    return masked


def create_app(build: Callable[[], Awaitable[Services]]) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        services = await build()
        app.state.services = services
        try:
            yield
        finally:
            await services.http.aclose()

    app = FastAPI(title="legacy-bridge PII proxy", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz(request: Request) -> Response:
        services: Services = request.app.state.services
        try:
            await services.vault.ping()
        except VaultUnavailable as e:
            return _error(503, f"vault unavailable: {e}", "fail_closed")
        return JSONResponse(
            {"status": "ok", "models": services.registry.models, "audit_head": services.audit.head}
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        services: Services = request.app.state.services
        task_id = request.headers.get(TASK_HEADER)
        if not task_id:
            return _error(400, f"missing {TASK_HEADER} header", "invalid_request_error")
        body = await request.json()
        provider = services.registry.provider_for(str(body.get("model", "")))
        if provider is None:
            return _error(400, f"unknown model {body.get('model')!r}", "invalid_request_error")
        rehydrate = request.headers.get(REHYDRATE_HEADER) == "final"

        try:
            masked = await mask_request(body, services.masker, task_id, provider.name)
        except UnsupportedContent as e:
            return _error(422, str(e), "invalid_request_error")
        except (DetectionError, VaultUnavailable, TokenCollision) as e:
            return _error(503, f"PII protection unavailable, request not forwarded: {e}", "fail_closed")

        upstream = services.http.build_request(
            "POST", f"{provider.base_url}/chat/completions", json=masked, headers=_auth(provider)
        )
        if masked.get("stream"):
            return await _stream(services, upstream, task_id, rehydrate)
        response = await services.http.send(upstream)
        if response.status_code != 200 or not rehydrate:
            return Response(response.content, status_code=response.status_code, media_type="application/json")
        payload = response.json()
        try:
            for choice in payload.get("choices", []):
                message = choice.get("message", {})
                if isinstance(message.get("content"), str):
                    message["content"] = await services.rehydrator.text(
                        message["content"], task_id, CLIENT_DESTINATION
                    )
        except VaultUnavailable as e:
            return _error(503, f"vault unavailable during rehydration: {e}", "fail_closed")
        return JSONResponse(payload)

    return app


def _auth(provider: Provider) -> dict[str, str]:
    return {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {}


async def _stream(services: Services, upstream: httpx.Request, task_id: str, rehydrate: bool) -> Response:
    response = await services.http.send(upstream, stream=True)
    if response.status_code != 200:
        content = await response.aread()
        await response.aclose()
        return Response(content, status_code=response.status_code, media_type="application/json")

    async def events() -> AsyncIterator[bytes]:
        streams: dict[int, StreamRehydrator] = {}
        try:
            async for line in response.aiter_lines():
                if not rehydrate or not line.startswith("data: "):
                    yield (line + "\n").encode()
                    continue
                data = line[len("data: ") :]
                if data.strip() == "[DONE]":
                    leftover = {i: await s.finish() for i, s in streams.items()}
                    if any(leftover.values()):
                        flush = {
                            "choices": [
                                {"index": i, "delta": {"content": t}} for i, t in leftover.items() if t
                            ]
                        }
                        yield f"data: {json.dumps(flush, ensure_ascii=False)}\n\n".encode()
                    yield (line + "\n").encode()
                    continue
                event = json.loads(data)
                for choice in event.get("choices", []):
                    index = choice.get("index", 0)
                    stream = streams.setdefault(
                        index, StreamRehydrator(services.rehydrator, task_id, CLIENT_DESTINATION)
                    )
                    delta = choice.setdefault("delta", {})
                    text = (
                        await stream.feed(delta["content"]) if isinstance(delta.get("content"), str) else ""
                    )
                    if choice.get("finish_reason"):
                        text += await stream.finish()
                    if "content" in delta or text:
                        delta["content"] = text
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n".encode()
        finally:
            await response.aclose()

    return StreamingResponse(events(), media_type="text/event-stream")


async def build_services() -> Services:
    settings = load_settings()
    policy = load_policy()
    vault = RedisVault(
        aioredis.Redis.from_url(settings.redis_url),
        Sealer(settings.vault_master_key),
        settings.vault_task_ttl_seconds,
    )
    audit = AuditLog(settings.audit_path)
    masker = Masker(
        build_detector(settings.detector, policy),
        TokenMinter(settings.token_key, settings.run_seed),
        vault,
        audit,
        policy,
    )
    return Services(
        masker=masker,
        rehydrator=Rehydrator(vault, audit),
        vault=vault,
        registry=load_registry(),
        http=httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)),
        audit=audit,
    )


app = create_app(build_services)
