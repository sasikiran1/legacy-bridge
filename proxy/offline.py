"""Run the proxy against a recording provider instead of a real one (leak check, benchmarks, demo, tests).

The recording provider captures every outbound payload and replies with an echo of the prompt. The reply
quotes the last user message (still tokenized), so rehydration has something to restore; streaming replies
are cut into short deltas, which splits tokens across SSE events.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from proxy.app import Services
from proxy.audit import AuditLog
from proxy.detection import Detector
from proxy.masking import Masker
from proxy.policy import PiiPolicy, load_policy
from proxy.providers import Provider, Registry
from proxy.rehydrate import Rehydrator
from proxy.tokens import TokenMinter
from proxy.vault import MemoryVault, Sealer, Vault

RECORDING_MODEL = "recording-model"
SYNTHETIC_TOKEN_KEY = b"legacy-bridge synthetic token k1"
"""The published token key from .env.example (ADR 0001); fine only because all PII here is synthetic."""


@dataclass
class RecordingProvider:
    chunk_size: int = 3
    bodies: list[bytes] = field(default_factory=list)

    def reply_for(self, body: dict[str, object]) -> str:
        messages = body.get("messages")
        assert isinstance(messages, list)
        last = next((m for m in reversed(messages) if m.get("role") == "user"), None)
        content = last.get("content") if last else ""
        return (
            f"You asked: {content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)}"
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(request.content)
        body = json.loads(request.content)
        reply = self.reply_for(body)
        if not body.get("stream"):
            message = {"role": "assistant", "content": reply}
            return httpx.Response(
                200,
                json={
                    "id": "cmpl-recording",
                    "object": "chat.completion",
                    "model": body.get("model"),
                    "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                },
            )
        events = []
        for i in range(0, len(reply), self.chunk_size):
            delta = {"content": reply[i : i + self.chunk_size]}
            events.append({"choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
        events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        payload = (
            "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events) + "data: [DONE]\n\n"
        )
        return httpx.Response(200, content=payload.encode(), headers={"content-type": "text/event-stream"})

    def recorded_text(self) -> str:
        """Every recorded body, JSON-decoded and re-serialized without ASCII escaping.

        httpx sends JSON with `\\uXXXX` escapes, so a raw byte search would miss non-ASCII canaries.
        """
        return "\n".join(json.dumps(json.loads(body), ensure_ascii=False) for body in self.bodies)


def offline_services(
    detector: Detector,
    provider: RecordingProvider,
    audit_path: Path,
    vault: Vault | None = None,
    policy: PiiPolicy | None = None,
    vault_key: bytes = bytes(range(32)),
) -> Services:
    policy = policy or load_policy()
    vault = vault or MemoryVault(Sealer(vault_key))
    audit = AuditLog(audit_path)
    masker = Masker(detector, TokenMinter(SYNTHETIC_TOKEN_KEY, "7"), vault, audit, policy)
    registry = Registry(
        {
            "recording": Provider(
                name="recording",
                base_url="http://recording.invalid/v1",
                api_key=None,
                trust="remote_untrusted",
            )
        },
        {RECORDING_MODEL: "recording"},
    )
    return Services(
        masker=masker,
        rehydrator=Rehydrator(vault, audit),
        vault=vault,
        registry=registry,
        http=httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)),
        audit=audit,
    )
