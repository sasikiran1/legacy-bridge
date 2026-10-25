"""OpenAI-compatible chat completions proxy that tokenizes PII before anything leaves for a model.

    uv run uvicorn proxy.app:app --port 8090

Headers:
    X-LB-Task-Id      required; scopes tokens, the vault, the audit trail and cassettes
    X-LB-Call         the caller's 0-based LLM call index within the task (cassette position)
    X-LB-Rehydrate    `final` restores real values in the returned text (operator-facing output only);
                      by default responses keep tokens, since the agent loop itself stays in token space

`model` may be a concrete model id or a role from config/models.yaml; roles fall back along their chain
on 429, 5xx or a spent rate budget (proxy/routing.py). LLM_MODE selects live, record or replay
(proxy/cassettes.py).
Responses carry `x-lb-model` (who answered) and, in replay, `x-lb-cassette: hit|mismatch`.

Fails closed: if detection or the vault is unavailable, the request is not forwarded (503).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from proxy.audit import AuditLog
from proxy.cassettes import CassetteMiss, CassetteStore, LlmMode, request_digest
from proxy.detection import DetectionError, RegexDetector, build_detector
from proxy.masking import Masker
from proxy.policy import load_policy
from proxy.providers import Provider, Registry, load_registry
from proxy.rehydrate import Rehydrator, StreamRehydrator
from proxy.routing import Router, estimate_tokens, load_router
from proxy.settings import load_settings
from proxy.tokens import TokenMinter
from proxy.vault import RedisVault, Sealer, TokenCollision, Vault, VaultUnavailable
from proxy.vision import decode_data_url, encode_png, redact_image

TASK_HEADER = "x-lb-task-id"
CALL_HEADER = "x-lb-call"
REHYDRATE_HEADER = "x-lb-rehydrate"
CLIENT_DESTINATION = "client"
AGENT_DESTINATION = "agent-state"
RETRYABLE = {429, 500, 502, 503, 504}
LOCAL_RETRIES = 1
LOCAL_RETRY_SECONDS = 5.0

Recording = tuple[str, int, dict[str, Any], str]


@dataclass
class Services:
    masker: Masker
    rehydrator: Rehydrator
    vault: Vault
    registry: Registry
    http: httpx.AsyncClient
    audit: AuditLog
    router: Router = field(default_factory=lambda: Router({}, {}))
    mode: LlmMode = LlmMode.LIVE
    cassettes: CassetteStore = field(default_factory=CassetteStore)


class UnsupportedContent(Exception):
    pass


def _error(status: int, message: str, kind: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"message": message, "type": kind}})


async def mask_request(
    body: dict[str, Any], masker: Masker, task_id: str, destination: str
) -> dict[str, Any]:
    """Masks every message. User, system and tool messages go first so that values they introduce are known
    before the model-authored (assistant) messages, which get pattern rules and known values but no NER."""
    originals = list(body.get("messages", []))
    masked_messages: list[dict[str, Any]] = [{} for _ in originals]
    order = sorted(range(len(originals)), key=lambda i: originals[i].get("role") == "assistant")
    for index in order:
        message = dict(originals[index])
        authored = message.get("role") == "assistant"
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = await masker.mask_content(content, task_id, destination, authored)
        elif isinstance(content, list):
            parts = []
            for part in content:
                if part.get("type") == "text":
                    text = await masker.mask_content(part["text"], task_id, destination, authored)
                    parts.append({**part, "text": text})
                elif part.get("type") == "image_url" and not authored:
                    url = str((part.get("image_url") or {}).get("url", ""))
                    if not url.startswith("data:"):
                        raise UnsupportedContent("images must be inline data URLs; the proxy fetches nothing")
                    redacted, _ = await redact_image(decode_data_url(url), masker, task_id, destination)
                    parts.append({**part, "image_url": {**part["image_url"], "url": encode_png(redacted)}})
                else:
                    raise UnsupportedContent(f"unsupported content part {part.get('type')!r}")
            message["content"] = parts
        if message.get("tool_calls"):
            calls = []
            for call in message["tool_calls"]:
                function = dict(call.get("function", {}))
                if isinstance(function.get("arguments"), str):
                    function["arguments"] = await masker.mask_content(
                        function["arguments"], task_id, destination, authored
                    )
                calls.append({**call, "function": function})
            message["tool_calls"] = calls
        masked_messages[index] = message
    return {**body, "messages": masked_messages}


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
            {
                "status": "ok",
                "mode": services.mode.value,
                "models": services.registry.models,
                "audit_head": services.audit.head,
            }
        )

    @app.post("/v1/mask")
    async def mask(request: Request) -> Response:
        """Masks tool results for the executors, so agent state, checkpoints and traces hold tokens only.

        Body: {"content": "<text or JSON document>"} or {"value": <any JSON value>}.
        """
        services: Services = request.app.state.services
        task_id = request.headers.get(TASK_HEADER)
        if not task_id:
            return _error(400, f"missing {TASK_HEADER} header", "invalid_request_error")
        payload = await request.json()
        try:
            if "value" in payload:
                masked = await services.masker.mask_value(payload["value"], task_id, AGENT_DESTINATION)
            else:
                masked = await services.masker.mask_content(
                    str(payload.get("content", "")), task_id, AGENT_DESTINATION
                )
        except (DetectionError, VaultUnavailable, TokenCollision) as e:
            return _error(503, f"PII protection unavailable: {e}", "fail_closed")
        return JSONResponse({"masked": masked})

    @app.delete("/v1/tasks/{task_id:path}")
    async def forget_task(task_id: str, request: Request) -> Response:
        """Drops a task's vault entries and cached masks, so a rerun of the same task starts clean."""
        services: Services = request.app.state.services
        services.masker.forget(task_id)
        try:
            await services.vault.drop(task_id)
        except VaultUnavailable as e:
            return _error(503, f"vault unavailable: {e}", "fail_closed")
        return Response(status_code=204)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        services: Services = request.app.state.services
        task_id = request.headers.get(TASK_HEADER)
        if not task_id:
            return _error(400, f"missing {TASK_HEADER} header", "invalid_request_error")
        call = int(request.headers.get(CALL_HEADER, "0"))
        body = await request.json()
        requested = str(body.get("model", ""))
        candidates = [
            (model, provider)
            for model in services.router.chain(requested)
            if (provider := services.registry.provider_for(model)) is not None
        ]
        if not candidates and services.mode is not LlmMode.REPLAY:
            return _error(400, f"unknown model or role {requested!r}", "invalid_request_error")
        destination = candidates[0][1].name if candidates else "replay"
        rehydrate = request.headers.get(REHYDRATE_HEADER) == "final"

        try:
            masked = await mask_request(body, services.masker, task_id, destination)
        except UnsupportedContent as e:
            return _error(422, str(e), "invalid_request_error")
        except (DetectionError, VaultUnavailable, TokenCollision) as e:
            return _error(503, f"PII protection unavailable, request not forwarded: {e}", "fail_closed")

        if services.mode is LlmMode.REPLAY:
            return await _replay(services, task_id, call, masked, rehydrate)

        tokens = estimate_tokens(masked)
        for model, provider in candidates:
            if not await services.router.acquire(model, tokens):
                continue
            attempts = 1 + LOCAL_RETRIES * (provider.trust == "local")
            for attempt in range(attempts):
                if attempt:
                    # Ollama's runner can crash mid-request (a CUDA error) and restarts on the next one.
                    await asyncio.sleep(LOCAL_RETRY_SECONDS)
                upstream = services.http.build_request(
                    "POST",
                    f"{provider.base_url}/chat/completions",
                    json={**masked, "model": model},
                    headers=_auth(provider),
                )
                response = await services.http.send(upstream, stream=bool(masked.get("stream")))
                if response.status_code < 500 or attempt == attempts - 1:
                    break
                await response.aclose()
            if response.status_code in RETRYABLE:
                await response.aclose()
                continue
            record: Recording | None = (
                (task_id, call, masked, model) if services.mode is LlmMode.RECORD else None
            )
            headers = {"x-lb-model": model}
            if masked.get("stream") and response.status_code == 200:
                return _sse(services, _upstream_lines(response), task_id, rehydrate, headers, record)
            content = await response.aread()
            await response.aclose()
            if record:
                services.cassettes.save(
                    task_id, call, masked, model, response.status_code, False, content.decode()
                )
            return await _completion(services, response.status_code, content, task_id, rehydrate, headers)
        return _error(429, f"every model for {requested!r} is rate-limited or failing", "rate_limit_exceeded")

    return app


def _auth(provider: Provider) -> dict[str, str]:
    return {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {}


async def _replay(
    services: Services, task_id: str, call: int, masked: dict[str, Any], rehydrate: bool
) -> Response:
    try:
        exchange = services.cassettes.load(task_id, call)
    except CassetteMiss as e:
        return _error(424, str(e), "cassette_miss")
    hit = exchange.request_sha256 == request_digest(masked)
    headers = {"x-lb-model": exchange.served_by, "x-lb-cassette": "hit" if hit else "mismatch"}
    if not hit and (debug_dir := os.environ.get("CASSETTE_MISMATCH_DIR")):
        # The replayed request (tokens only, like the cassette), for diffing against the recording.
        path = Path(debug_dir) / task_id.replace("/", "_") / f"{call:03d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(masked, indent=1, ensure_ascii=False), encoding="utf-8")
    if exchange.stream:

        async def lines() -> AsyncIterator[str]:
            for line in exchange.body.split("\n"):
                yield line

        return _sse(services, lines(), task_id, rehydrate, headers, None)
    return await _completion(services, exchange.status, exchange.body.encode(), task_id, rehydrate, headers)


async def _completion(
    services: Services, status: int, content: bytes, task_id: str, rehydrate: bool, headers: dict[str, str]
) -> Response:
    if status != 200 or not rehydrate:
        return Response(content, status_code=status, media_type="application/json", headers=headers)
    payload = json.loads(content)
    try:
        for choice in payload.get("choices", []):
            message = choice.get("message", {})
            if isinstance(message.get("content"), str):
                message["content"] = await services.rehydrator.text(
                    message["content"], task_id, CLIENT_DESTINATION
                )
    except VaultUnavailable as e:
        return _error(503, f"vault unavailable during rehydration: {e}", "fail_closed")
    return JSONResponse(payload, headers=headers)


async def _upstream_lines(response: httpx.Response) -> AsyncIterator[str]:
    try:
        async for line in response.aiter_lines():
            yield line
    finally:
        await response.aclose()


def _sse(
    services: Services,
    lines: AsyncIterator[str],
    task_id: str,
    rehydrate: bool,
    headers: dict[str, str],
    record: Recording | None,
) -> StreamingResponse:
    async def events() -> AsyncIterator[bytes]:
        streams: dict[int, StreamRehydrator] = {}
        raw: list[str] = []
        async for line in lines:
            raw.append(line)
            if not rehydrate or not line.startswith("data: "):
                yield (line + "\n").encode()
                continue
            data = line[len("data: ") :]
            if data.strip() == "[DONE]":
                leftover = {i: await s.finish() for i, s in streams.items()}
                if any(leftover.values()):
                    flush = {
                        "choices": [{"index": i, "delta": {"content": t}} for i, t in leftover.items() if t]
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
                text = await stream.feed(delta["content"]) if isinstance(delta.get("content"), str) else ""
                if choice.get("finish_reason"):
                    text += await stream.finish()
                if "content" in delta or text:
                    delta["content"] = text
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n".encode()
        if record:
            task, call, masked, model = record
            services.cassettes.save(task, call, masked, model, 200, True, "\n".join(raw))

    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)


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
        light_detector=RegexDetector(),
    )
    local_text = os.environ.get("LOCAL_TEXT_MODEL") or None
    return Services(
        masker=masker,
        rehydrator=Rehydrator(vault, audit),
        vault=vault,
        registry=load_registry(local_text=local_text),
        http=httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)),
        audit=audit,
        router=load_router(local_text=local_text),
        mode=LlmMode(os.environ.get("LLM_MODE", LlmMode.REPLAY.value)),
    )


app = create_app(build_services)
