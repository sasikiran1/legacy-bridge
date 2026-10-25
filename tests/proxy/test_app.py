from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from proxy.app import Services, create_app
from proxy.offline import RecordingProvider
from tests.proxy.fakes import MOCK_MODEL, services, tokens_in

PROMPT = "Check claim CLM-2025-104233 for member M1234567, SSN 123-45-6789."
RAW = ["CLM-2025-104233", "M1234567", "123-45-6789"]


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.upstream = RecordingProvider()
        self.services, self.vault = services(tmp_path, self.upstream)

    async def build(self) -> Services:
        return self.services


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[tuple[Harness, httpx.AsyncClient]]:
    h = Harness(tmp_path)
    app: FastAPI = create_app(h.build)
    app.state.services = h.services
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy") as client:
        yield h, client
    await h.services.http.aclose()


def _body(stream: bool = False, content: object = PROMPT) -> dict[str, object]:
    return {"model": MOCK_MODEL, "stream": stream, "messages": [{"role": "user", "content": content}]}


async def test_upstream_sees_tokens_and_client_gets_tokens_by_default(
    harness: tuple[Harness, httpx.AsyncClient],
) -> None:
    h, client = harness
    response = await client.post("/v1/chat/completions", json=_body(), headers={"X-LB-Task-Id": "t1"})
    assert response.status_code == 200
    sent = h.upstream.recorded_text()
    assert not any(value in sent for value in RAW)
    reply = response.json()["choices"][0]["message"]["content"]
    assert len(tokens_in(reply)) == 3


async def test_final_rehydration_restores_values(harness: tuple[Harness, httpx.AsyncClient]) -> None:
    _, client = harness
    response = await client.post(
        "/v1/chat/completions", json=_body(), headers={"X-LB-Task-Id": "t1", "X-LB-Rehydrate": "final"}
    )
    assert response.json()["choices"][0]["message"]["content"] == f"You asked: {PROMPT}"


async def test_streaming_rehydration_never_emits_a_partial_token(
    harness: tuple[Harness, httpx.AsyncClient],
) -> None:
    h, client = harness
    deltas = []
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json=_body(stream=True),
        headers={"X-LB-Task-Id": "t1", "X-LB-Rehydrate": "final"},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                for choice in json.loads(line[6:])["choices"]:
                    deltas.append(choice.get("delta", {}).get("content") or "")
    assert "".join(deltas) == f"You asked: {PROMPT}"
    assert not any("[[" in d for d in deltas)
    assert not any(value in h.upstream.recorded_text() for value in RAW)


async def test_tool_call_arguments_are_masked(harness: tuple[Harness, httpx.AsyncClient]) -> None:
    h, client = harness
    body = {
        "model": MOCK_MODEL,
        "messages": [
            {"role": "user", "content": "look it up"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "getMember", "arguments": '{"memberNumber": "M1234567"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": '{"memberNumber": "M1234567", "lastName": "Lovelace"}',
            },
        ],
    }
    assert (
        await client.post("/v1/chat/completions", json=body, headers={"X-LB-Task-Id": "t1"})
    ).status_code == 200
    sent = h.upstream.recorded_text()
    assert "M1234567" not in sent
    assert "Lovelace" not in sent


async def test_fails_closed_when_the_vault_is_down(harness: tuple[Harness, httpx.AsyncClient]) -> None:
    h, client = harness
    h.vault.available = False
    response = await client.post("/v1/chat/completions", json=_body(), headers={"X-LB-Task-Id": "t1"})
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "fail_closed"
    assert h.upstream.bodies == []


@pytest.mark.parametrize(
    ("headers", "body", "status"),
    [
        ({}, _body(), 400),
        ({"X-LB-Task-Id": "t1"}, {**_body(), "model": "nope"}, 400),
        ({"X-LB-Task-Id": "t1"}, _body(content=[{"type": "image_url", "image_url": {"url": "x"}}]), 422),
    ],
)
async def test_rejects_requests_it_cannot_protect(
    harness: tuple[Harness, httpx.AsyncClient], headers: dict[str, str], body: dict[str, object], status: int
) -> None:
    h, client = harness
    assert (await client.post("/v1/chat/completions", json=body, headers=headers)).status_code == status
    assert h.upstream.bodies == []


async def test_mask_endpoint_tokenizes_tool_results_for_agent_state(
    harness: tuple[Harness, httpx.AsyncClient],
) -> None:
    h, client = harness
    response = await client.post(
        "/v1/mask",
        json={"content": '{"memberNumber": "M1234567", "lastName": "Lovelace"}'},
        headers={"X-LB-Task-Id": "t1"},
    )
    masked = response.json()["masked"]
    assert "M1234567" not in masked
    assert "Lovelace" not in masked
    assert h.upstream.bodies == []
