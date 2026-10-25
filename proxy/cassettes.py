"""Recorded LLM exchanges, so reruns are free and CI never calls a model.

    LLM_MODE=live     forward to the provider, record nothing
    LLM_MODE=record   forward to the provider and store each exchange
    LLM_MODE=replay   serve stored exchanges; never contact a provider

Exchanges are stored per task in call order: cassettes/<task>/<call>.json. They hold the request exactly as
the provider would have received it (already masked) and the provider's response, so they contain tokens only;
the leak test scans them for canaries. Replay matches by (task, call index), not by an exact hash of the
request: GLiNER on CPU is not bit-for-bit deterministic, so one run can mask a borderline span differently.
A replayed request that differs from the recorded one is still served, flagged and counted, so drift stays
visible.

Screenshots (already redacted) are stored beside the exchange as `<call>-<sha256>.png`, and the request
holds `cassette-image:<sha256>` in their place, so cassettes stay readable and the leak test can OCR images.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict

CASSETTE_DIR = Path(__file__).resolve().parent.parent / "cassettes"


class LlmMode(StrEnum):
    LIVE = "live"
    RECORD = "record"
    REPLAY = "replay"


class Exchange(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    call: int
    request_sha256: str
    request: dict[str, object]
    served_by: str
    status: int
    stream: bool
    body: str
    recorded_at: str


class CassetteMiss(Exception):
    pass


IMAGE_REF = "cassette-image:"
_DATA_URL = re.compile(r"data:image/[a-z]+;base64,(?P<data>[A-Za-z0-9+/=]+)")


def externalize_images(body: object, images: dict[str, bytes] | None = None) -> object:
    """`body` with every inline image replaced by a reference to its SHA-256; `images` collects the bytes."""
    match body:
        case str() if m := _DATA_URL.fullmatch(body):
            data = base64.b64decode(m["data"])
            digest = hashlib.sha256(data).hexdigest()
            if images is not None:
                images[digest] = data
            return IMAGE_REF + digest
        case list():
            return [externalize_images(item, images) for item in body]
        case dict():
            return {k: externalize_images(v, images) for k, v in body.items()}
    return body


def request_digest(body: dict[str, object]) -> str:
    canonical = json.dumps(
        externalize_images(body), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _safe(task_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", task_id)


class CassetteStore:
    def __init__(self, root: Path = CASSETTE_DIR) -> None:
        self.root = root

    def path(self, task_id: str, call: int) -> Path:
        return self.root / _safe(task_id) / f"{call:03d}.json"

    def save(
        self,
        task_id: str,
        call: int,
        request: dict[str, object],
        served_by: str,
        status: int,
        stream: bool,
        body: str,
    ) -> Exchange:
        images: dict[str, bytes] = {}
        stored = cast(dict[str, object], externalize_images(request, images))
        exchange = Exchange(
            task_id=task_id,
            call=call,
            request_sha256=request_digest(request),
            request=stored,
            served_by=served_by,
            status=status,
            stream=stream,
            body=body,
            recorded_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        )
        path = self.path(task_id, call)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(exchange.model_dump_json(indent=1) + "\n", encoding="utf-8")
        for digest, data in images.items():
            (path.parent / f"{call:03d}-{digest}.png").write_bytes(data)
        return exchange

    def load(self, task_id: str, call: int) -> Exchange:
        path = self.path(task_id, call)
        if not path.exists():
            raise CassetteMiss(f"no recorded exchange {call} for task {task_id}")
        return Exchange.model_validate_json(path.read_text(encoding="utf-8"))

    def clear(self, task_id: str) -> None:
        directory = self.root / _safe(task_id)
        if directory.exists():
            for file in [*directory.glob("*.json"), *directory.glob("*.png")]:
                file.unlink()
