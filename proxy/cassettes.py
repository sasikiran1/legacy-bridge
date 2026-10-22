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
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path

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


def request_digest(body: dict[str, object]) -> str:
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
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
        exchange = Exchange(
            task_id=task_id,
            call=call,
            request_sha256=request_digest(request),
            request=request,
            served_by=served_by,
            status=status,
            stream=stream,
            body=body,
            recorded_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        )
        path = self.path(task_id, call)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(exchange.model_dump_json(indent=1) + "\n", encoding="utf-8")
        return exchange

    def load(self, task_id: str, call: int) -> Exchange:
        path = self.path(task_id, call)
        if not path.exists():
            raise CassetteMiss(f"no recorded exchange {call} for task {task_id}")
        return Exchange.model_validate_json(path.read_text(encoding="utf-8"))

    def clear(self, task_id: str) -> None:
        directory = self.root / _safe(task_id)
        if directory.exists():
            for file in directory.glob("*.json"):
                file.unlink()
