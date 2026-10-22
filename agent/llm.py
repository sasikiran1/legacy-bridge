"""Chat client for the PII proxy (OpenAI-compatible, tool calling). Every model call in the agent goes
through here."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from agent.telemetry import tracer


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str
    parse_error: str | None = None


class LlmReply(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str | None
    tool_calls: tuple[ToolCall, ...]
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cassette: str | None
    finish_reason: str | None

    def assistant_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            message["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.raw_arguments}}
                for c in self.tool_calls
            ]
        return message


class LlmError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status


class LlmClient:
    def __init__(self, proxy_url: str, http: httpx.AsyncClient | None = None) -> None:
        self._url = f"{proxy_url}/v1/chat/completions"
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def chat(
        self,
        task_id: str,
        call: int,
        role: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
    ) -> LlmReply:
        body: dict[str, Any] = {
            "model": role,
            "messages": messages,
            "temperature": 0,
            "seed": 7,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
        with tracer().start_as_current_span(f"chat {role}") as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.request.model", role)
            span.set_attribute("gen_ai.request.temperature", 0.0)
            span.set_attribute("gen_ai.request.max_tokens", max_tokens)
            span.set_attribute("legacy_bridge.task_id", task_id)
            span.set_attribute("legacy_bridge.call", call)
            started = time.perf_counter()
            response = await self._http.post(
                self._url, json=body, headers={"X-LB-Task-Id": task_id, "X-LB-Call": str(call)}
            )
            latency = 1000 * (time.perf_counter() - started)
            if response.status_code != 200:
                raise LlmError(response.status_code, response.text[:500])
            payload = response.json()
            choice = payload["choices"][0]
            message = choice.get("message", {})
            usage = payload.get("usage") or {}
            reply = LlmReply(
                content=message.get("content"),
                tool_calls=tuple(_tool_call(c) for c in message.get("tool_calls") or []),
                model=response.headers.get("x-lb-model", payload.get("model", role)),
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
                latency_ms=latency,
                cassette=response.headers.get("x-lb-cassette"),
                finish_reason=choice.get("finish_reason"),
            )
            span.set_attribute("gen_ai.response.model", reply.model)
            span.set_attribute("gen_ai.usage.input_tokens", reply.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", reply.output_tokens)
            span.set_attribute("gen_ai.response.finish_reasons", [reply.finish_reason or "unknown"])
            return reply


def _tool_call(raw: dict[str, Any]) -> ToolCall:
    function = raw.get("function", {})
    text = function.get("arguments") or "{}"
    try:
        arguments = json.loads(text) if isinstance(text, str) else dict(text)
        error = None if isinstance(arguments, dict) else "arguments must be a JSON object"
    except ValueError as e:
        arguments, error = {}, f"arguments are not valid JSON: {e}"
    return ToolCall(
        id=raw.get("id") or f"call-{function.get('name', 'tool')}",
        name=function.get("name", ""),
        arguments=arguments if isinstance(arguments, dict) else {},
        raw_arguments=text if isinstance(text, str) else json.dumps(text),
        parse_error=error,
    )
