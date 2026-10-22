"""Canary leak check: push every canary member's documents through the proxy and search what left.

    uv run python -m analysis.leak_check [--detectors full,regex] [--scopes member,document]

The provider is a recorder, so the check sees exactly what a real provider would receive. Documents go in as
the agent would send them: instructions and exports as user messages, API JSON as tool results. Any canary
value found in a recorded payload is a leak.

Scopes: `member` sends all of a member's documents within one task, as an agent session would, so values
masked from structured API JSON are re-caught later by the known-values pass. `document` gives every document
its own task, which measures the detector alone. The CI gate (tests/leak) requires zero for `full` + `member`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict

from analysis.manifest import Run
from analysis.pii_corpus import Document, build_corpus
from proxy.app import TASK_HEADER, Services, create_app
from proxy.detection import GLINER_MODEL, Detector, DetectorConfig, build_detector
from proxy.offline import RECORDING_MODEL, offline_services
from proxy.policy import load_policy
from proxy.recording import RecordingProvider
from testbed.seed import generate

JSON_SURFACES = {"api_json", "api_drift_json"}


class Leak(BaseModel):
    model_config = ConfigDict(frozen=True)

    entity_type: str
    member_number: str
    surfaces: tuple[str, ...]


Scope = Literal["member", "document"]


class LeakReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    detector: str
    scope: Scope
    canaries: int
    documents: int
    requests: int
    leaked: tuple[Leak, ...]


def _messages(doc: Document) -> list[dict[str, object]]:
    if doc.surface in JSON_SURFACES:
        call = {"id": "call-1", "type": "function", "function": {"name": "getMember", "arguments": "{}"}}
        return [
            {"role": "user", "content": "Look up the member."},
            {"role": "assistant", "content": None, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": "call-1", "content": doc.text},
        ]
    return [
        {"role": "system", "content": "You operate the member services portal."},
        {"role": "user", "content": doc.text},
    ]


async def check(detector: Detector, audit_dir: Path, scope: Scope = "member") -> LeakReport:
    ds, tasks, canaries = generate()
    canary_members = [m for m in ds.members if m.member_number in ds.canary_member_numbers]
    documents = build_corpus(ds, tasks, canary_members, seed=1)

    provider = RecordingProvider()
    services = offline_services(detector, provider, audit_dir / "audit.jsonl")

    async def ready() -> Services:
        return services

    app = create_app(ready)
    app.state.services = services
    surfaces_by_request: list[str] = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy") as client:
        for doc in documents:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": RECORDING_MODEL, "messages": _messages(doc)},
                headers={TASK_HEADER: f"leak-{doc.member_number if scope == 'member' else doc.id}"},
            )
            response.raise_for_status()
            surfaces_by_request.append(doc.surface)
    await services.http.aclose()

    payloads = [json.dumps(json.loads(b), ensure_ascii=False) for b in provider.bodies]
    leaked = []
    for canary in canaries:
        hits = tuple(sorted({surfaces_by_request[i] for i, p in enumerate(payloads) if canary.value in p}))
        if hits:
            leaked.append(
                Leak(entity_type=canary.entity_type, member_number=canary.member_number, surfaces=hits)
            )
    return LeakReport(
        detector=detector.name,
        scope=scope,
        canaries=len(canaries),
        documents=len(documents),
        requests=len(payloads),
        leaked=tuple(leaked),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analysis.leak_check")
    parser.add_argument("--detectors", default=",".join(c.value for c in DetectorConfig))
    parser.add_argument("--scopes", default="member,document")
    args = parser.parse_args(argv)
    configs = [DetectorConfig(name) for name in args.detectors.split(",")]
    scopes: list[Scope] = [s for s in ("member", "document") if s in args.scopes.split(",")]

    run = Run(kind="leak-check", config={"detectors": [c.value for c in configs], "scopes": scopes})
    reports = []
    policy = load_policy()
    for config in configs:
        detector = build_detector(config, policy)
        for scope in scopes:
            with tempfile.TemporaryDirectory() as tmp:
                report = asyncio.run(check(detector, Path(tmp), scope))
            reports.append(report)
            print(f"{report.detector:16} {report.scope:9} {len(report.leaked):3} of {report.canaries} leaked")
    run.write("leak.json", [r.model_dump(mode="json") for r in reports])
    run.finish(models={"gliner": GLINER_MODEL, "spacy": "en_core_web_lg-3.8.0"})
    print(f"-> {run.directory}")
    gate = [r for r in reports if r.detector == DetectorConfig.FULL.value and r.scope == "member"]
    return 1 if any(r.leaked for r in gate) else 0


if __name__ == "__main__":
    sys.exit(main())
