"""A recording mock provider: captures every outbound payload and replies with an echo of the prompt.

Used by the canary leak check, the latency benchmark, the demo and the tests. The reply quotes the last user
message (still tokenized), so rehydration has something to restore; streaming replies are cut into short
deltas, which splits tokens across SSE events.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx


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
