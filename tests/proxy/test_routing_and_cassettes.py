from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from proxy import app as app_module
from proxy.app import CALL_HEADER, REHYDRATE_HEADER, TASK_HEADER, Services, create_app
from proxy.cassettes import CassetteStore, LlmMode
from proxy.providers import Provider, Registry
from proxy.recording import RecordingProvider
from proxy.routing import Budget, Limits, Router
from tests.proxy.fakes import kit

PROMPT = "Check claim CLM-2025-104233 for member M1234567."


def test_budget_blocks_the_minute_and_the_day() -> None:
    budget = Budget(Limits(rpm=2, rpd=3))
    assert budget.wait_seconds(10, now=0) == 0
    budget.spend(10, now=0)
    budget.spend(10, now=1)
    assert budget.wait_seconds(10, now=2) == pytest.approx(58)
    assert budget.wait_seconds(10, now=61) == 0
    budget.spend(10, now=61)
    assert budget.wait_seconds(10, now=200) is None


class Upstreams:
    """Provider `a` always answers 429; provider `b` is a recorder."""

    def __init__(self) -> None:
        self.recorder = RecordingProvider()
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.host)
        if request.url.host == "a.invalid":
            return httpx.Response(429, json={"error": "slow down"})
        return self.recorder.handler(request)


def _services(tmp_path: Path, upstreams: Upstreams, mode: LlmMode) -> Services:
    parts = kit(tmp_path)
    registry = Registry(
        {
            "a": Provider(name="a", base_url="http://a.invalid/v1", api_key=None, trust="remote_untrusted"),
            "b": Provider(name="b", base_url="http://b.invalid/v1", api_key=None, trust="remote_untrusted"),
        },
        {"model-a": "a", "model-b": "b"},
    )
    return Services(
        masker=parts.masker,
        rehydrator=parts.rehydrator,
        vault=parts.vault,
        registry=registry,
        http=httpx.AsyncClient(transport=httpx.MockTransport(upstreams.handler)),
        audit=parts.audit,
        router=Router({"explore": ["model-a", "model-b"]}, {}),
        mode=mode,
        cassettes=CassetteStore(tmp_path / "cassettes"),
    )


@pytest.fixture
async def client_for(tmp_path: Path) -> AsyncIterator[object]:
    clients: list[httpx.AsyncClient] = []

    async def make(services: Services) -> httpx.AsyncClient:
        app = create_app(lambda: _ready(services))
        app.state.services = services
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy")
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.aclose()


async def _ready(services: Services) -> Services:
    return services


async def _stream_text(client: httpx.AsyncClient, call: int, rehydrate: bool) -> tuple[str, httpx.Headers]:
    body = {"model": "explore", "stream": True, "messages": [{"role": "user", "content": PROMPT}]}
    headers = {TASK_HEADER: "t1", CALL_HEADER: str(call)}
    if rehydrate:
        headers[REHYDRATE_HEADER] = "final"
    text = []
    async with client.stream("POST", "/v1/chat/completions", json=body, headers=headers) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                for choice in json.loads(line[6:])["choices"]:
                    text.append(choice.get("delta", {}).get("content") or "")
        return "".join(text), response.headers


async def test_role_falls_back_on_429_and_records(tmp_path: Path, client_for: object) -> None:
    upstreams = Upstreams()
    client = await client_for(_services(tmp_path, upstreams, LlmMode.RECORD))  # type: ignore[operator]
    text, headers = await _stream_text(client, call=0, rehydrate=True)
    assert upstreams.calls == ["a.invalid", "b.invalid"]
    assert headers["x-lb-model"] == "model-b"
    assert text == f"You asked: {PROMPT}"
    recorded = (tmp_path / "cassettes" / "t1" / "000.json").read_text(encoding="utf-8")
    assert "M1234567" not in recorded
    assert "CLM-2025-104233" not in recorded


async def test_replay_serves_the_recording_without_any_provider(tmp_path: Path, client_for: object) -> None:
    recorder = await client_for(_services(tmp_path, Upstreams(), LlmMode.RECORD))  # type: ignore[operator]
    await _stream_text(recorder, call=0, rehydrate=False)

    upstreams = Upstreams()
    replayer = await client_for(_services(tmp_path, upstreams, LlmMode.REPLAY))  # type: ignore[operator]
    text, headers = await _stream_text(replayer, call=0, rehydrate=True)
    assert upstreams.calls == []
    assert headers["x-lb-cassette"] == "hit"
    assert text == f"You asked: {PROMPT}"


async def test_replay_flags_a_changed_request_and_misses_unknown_calls(
    tmp_path: Path, client_for: object
) -> None:
    recorder = await client_for(_services(tmp_path, Upstreams(), LlmMode.RECORD))  # type: ignore[operator]
    await _stream_text(recorder, call=0, rehydrate=False)
    replayer = await client_for(_services(tmp_path, Upstreams(), LlmMode.REPLAY))  # type: ignore[operator]

    changed = {
        "model": "explore",
        "stream": True,
        "messages": [{"role": "user", "content": "something else"}],
    }
    async with replayer.stream(
        "POST", "/v1/chat/completions", json=changed, headers={TASK_HEADER: "t1", CALL_HEADER: "0"}
    ) as response:
        assert response.headers["x-lb-cassette"] == "mismatch"
    missing = await replayer.post(
        "/v1/chat/completions", json=changed, headers={TASK_HEADER: "t1", CALL_HEADER: "7"}
    )
    assert missing.status_code == 424


class CrashOnce:
    """A local runner that crashes on its first request and recovers, as Ollama's CUDA runner does."""

    def __init__(self) -> None:
        self.recorder = RecordingProvider()
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls == 1:
            return httpx.Response(500, json={"error": "llama runner process has terminated"})
        return self.recorder.handler(request)


@pytest.mark.parametrize(
    ("trust", "expected_calls", "status"), [("local", 2, 200), ("remote_untrusted", 1, 429)]
)
async def test_only_a_local_runner_is_retried_after_a_crash(
    tmp_path: Path,
    client_for: object,
    monkeypatch: pytest.MonkeyPatch,
    trust: str,
    expected_calls: int,
    status: int,
) -> None:
    monkeypatch.setattr(app_module, "LOCAL_RETRY_SECONDS", 0.0)
    upstream = CrashOnce()
    parts = kit(tmp_path)
    services = Services(
        masker=parts.masker,
        rehydrator=parts.rehydrator,
        vault=parts.vault,
        registry=Registry(
            {"local": Provider(name="local", base_url="http://local.invalid/v1", api_key=None, trust=trust)},
            {"model-l": "local"},
        ),
        http=httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler)),
        audit=parts.audit,
        router=Router({"local_text": ["model-l"]}, {}),
        mode=LlmMode.LIVE,
        cassettes=CassetteStore(tmp_path / "cassettes"),
    )
    client = await client_for(services)  # type: ignore[operator]
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "local_text", "messages": [{"role": "user", "content": "hello"}]},
        headers={TASK_HEADER: "t1"},
    )
    assert response.status_code == status
    assert upstream.calls == expected_calls
