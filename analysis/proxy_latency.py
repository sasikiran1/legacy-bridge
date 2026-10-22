"""Latency the proxy adds: the same requests sent straight to an instant provider and through the proxy.

    uv run python -m analysis.proxy_latency [--members 30] [--detector full]

Runs in-process (ASGI), with the real Redis vault when REDIS_URL is set. The provider answers instantly, so
the difference is masking (detection on CPU, vault writes, audit appends) plus response handling. Detection
runs on CPU only; no GPU is used.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

import httpx
import redis.asyncio as aioredis

from analysis.manifest import Run
from analysis.pii_corpus import build_corpus
from proxy.app import TASK_HEADER, Services, create_app
from proxy.detection import GLINER_MODEL, DetectorConfig, build_detector
from proxy.offline import RECORDING_MODEL, offline_services
from proxy.policy import load_policy
from proxy.recording import RecordingProvider
from proxy.vault import RedisVault, Sealer, Vault
from testbed.seed import generate

BUCKETS = ((0, 500), (500, 2000), (2000, 10**9))


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(q * (len(ordered) - 1))))
    return ordered[index]


def summary(values: list[float]) -> dict[str, float | int]:
    return {
        "n": len(values),
        "p50_ms": 1000 * percentile(values, 0.50),
        "p95_ms": 1000 * percentile(values, 0.95),
        "mean_ms": 1000 * statistics.fmean(values),
    }


async def measure(members: int, config: DetectorConfig, audit_dir: Path) -> dict[str, object]:
    ds, tasks, _ = generate()
    pool = [m for m in ds.members if m.member_number not in ds.canary_member_numbers][:members]
    documents = build_corpus(ds, tasks, pool, seed=3)

    vault: Vault | None = None
    redis_client = None
    if os.environ.get("REDIS_URL"):
        redis_client = aioredis.Redis.from_url(os.environ["REDIS_URL"])
        vault = RedisVault(redis_client, Sealer(bytes(range(32))), ttl_seconds=600)

    provider = RecordingProvider()
    services = offline_services(
        build_detector(config, load_policy()), provider, audit_dir / "audit.jsonl", vault
    )

    async def ready() -> Services:
        return services

    app = create_app(ready)
    app.state.services = services
    direct_client = httpx.AsyncClient(transport=httpx.MockTransport(provider.handler))
    proxied: list[tuple[int, float]] = []
    direct: list[float] = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy") as client:
        warm = {"model": RECORDING_MODEL, "messages": [{"role": "user", "content": "warm up"}]}
        await client.post("/v1/chat/completions", json=warm, headers={TASK_HEADER: "latency-warmup"})
        for doc in documents:
            body = {"model": RECORDING_MODEL, "messages": [{"role": "user", "content": doc.text}]}
            started = time.perf_counter()
            await direct_client.post("http://recording.invalid/v1/chat/completions", json=body)
            direct.append(time.perf_counter() - started)
            started = time.perf_counter()
            response = await client.post(
                "/v1/chat/completions", json=body, headers={TASK_HEADER: f"latency-{doc.member_number}"}
            )
            response.raise_for_status()
            proxied.append((len(doc.text), time.perf_counter() - started))
    await direct_client.aclose()
    await services.http.aclose()
    if redis_client is not None:
        for member in pool:
            await redis_client.delete(f"vault:latency-{member.member_number}")
        await redis_client.aclose()

    added = [p - d for (_, p), d in zip(proxied, direct, strict=True)]
    by_length = {}
    for low, high in BUCKETS:
        values = [a for (length, _), a in zip(proxied, added, strict=True) if low <= length < high]
        if values:
            by_length[f"{low}-{high if high < 10**9 else 'max'}_chars"] = summary(values)
    return {
        "detector": config.value,
        "vault": "redis" if vault is not None else "memory",
        "requests": len(documents),
        "direct": summary(direct),
        "added": summary(added),
        "added_by_prompt_length": by_length,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analysis.proxy_latency")
    parser.add_argument("--members", type=int, default=30)
    parser.add_argument("--detector", default=DetectorConfig.FULL.value)
    args = parser.parse_args(argv)
    config = DetectorConfig(args.detector)

    run = Run(
        kind="proxy-latency", config={"members": args.members, "detector": config.value, "device": "cpu"}
    )
    with tempfile.TemporaryDirectory() as tmp:
        result = asyncio.run(measure(args.members, config, Path(tmp)))
    run.write("proxy_latency.json", result)
    run.finish(models={"gliner": GLINER_MODEL, "spacy": "en_core_web_lg-3.8.0"})
    added = result["added"]
    assert isinstance(added, dict)
    print(f"added latency p50={added['p50_ms']:.0f} ms p95={added['p95_ms']:.0f} ms -> {run.directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
