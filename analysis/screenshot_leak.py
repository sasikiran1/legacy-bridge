"""Screenshot redaction against the canaries (ADR 0008): real portal screenshots, OCR, production detector.

    uv run python -m analysis.screenshot_leak [--scopes document,member]

For every canary member, the member page is rendered by the running portal (healthy scenario) and captured
at the agent's viewport. Each screenshot is redacted by proxy.vision with the full detector, then OCR'd again.
A canary is `readable` if OCR finds its value on the raw screenshot (the ceiling for what redaction can
see) and `leaked` if it is still found on the redacted one.

Scopes: `document` redacts each screenshot in a fresh task (detector alone). `member` first masks the
member's API record in the same task, as the API tier would have, so known values are painted too.

Screenshots hold raw PII: they stay in memory and are never written. Results hold counts and canary indices.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from analysis.manifest import Run
from proxy.audit import AuditLog
from proxy.detection import GLINER_MODEL, DetectorConfig, build_detector
from proxy.masking import Masker
from proxy.offline import SYNTHETIC_TOKEN_KEY
from proxy.policy import load_policy
from proxy.tokens import TokenMinter
from proxy.vault import MemoryVault, Sealer
from proxy.vision import ocr_text, redact_image
from testbed.dataset import Member
from testbed.oracles import Oracle
from testbed.portal import PortalAdmin
from testbed.scenarios import load_scenarios
from testbed.seed import generate
from testbed.settings import settings

VIEWPORT = {"width": 1280, "height": 900}


def api_record(member: Member) -> dict[str, Any]:
    """The member as the API tier sees it, under the policy's structured field names."""
    return {
        "memberNumber": member.member_number,
        "firstName": member.first_name,
        "lastName": member.last_name,
        "fullName": member.full_name,
        "dateOfBirth": member.date_of_birth.isoformat(),
        "ssn": member.ssn,
        "email": member.email,
        "phone": member.phone,
        "street": member.street,
    }


async def screenshots(members: list[Member]) -> dict[str, bytes]:
    config = settings()
    healthy = load_scenarios()["healthy"].chaos
    async with PortalAdmin(config) as admin:
        await admin.apply(healthy)
    shots: dict[str, bytes] = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport=VIEWPORT)  # type: ignore[arg-type]
        oracle = Oracle(page, config.portal_base_url, healthy)
        await oracle.sign_in("clerk01", config.portal_test_user_password)
        for member in members:
            await oracle._open_member(member.member_number)
            await page.wait_for_load_state("networkidle")
            shots[member.member_number] = await page.screenshot()
        await browser.close()
    return shots


async def measure(
    scope: str, shots: dict[str, bytes], members: list[Member], workdir: Path
) -> dict[str, Any]:
    _, _, canaries = generate()
    policy = load_policy()
    masker = Masker(
        build_detector(DetectorConfig.FULL, policy),
        TokenMinter(SYNTHETIC_TOKEN_KEY, "7"),
        MemoryVault(Sealer(bytes(range(32)))),
        AuditLog(workdir / f"audit-{scope}.jsonl"),
        policy,
    )
    readable: Counter[str] = Counter()
    leaked: Counter[str] = Counter()
    painted = 0
    leaks: list[int] = []
    by_member = {m.member_number: m for m in members}
    for number, shot in shots.items():
        task = f"screenshot-{scope}-{number}"
        if scope == "member":
            await masker.mask_value(api_record(by_member[number]), task, "agent-state")
        raw_text = await ocr_text(shot)
        redacted, count = await redact_image(shot, masker, task, "local")
        painted += count
        redacted_text = await ocr_text(redacted)
        for index, canary in enumerate(canaries):
            if canary.member_number != number or canary.value not in raw_text:
                continue
            readable[canary.entity_type] += 1
            if canary.value in redacted_text:
                leaked[canary.entity_type] += 1
                leaks.append(index)
    return {
        "screenshots": len(shots),
        "values_painted": painted,
        "canaries_readable": sum(readable.values()),
        "canaries_leaked": sum(leaked.values()),
        "readable_by_type": dict(sorted(readable.items())),
        "leaked_by_type": dict(sorted(leaked.items())),
        "leaked_canaries": sorted(leaks),
    }


async def run(scopes: list[str]) -> int:
    ds, _, canaries = generate()
    members = [m for m in ds.members if m.member_number in ds.canary_member_numbers]
    run = Run(kind="screenshot-leak", config={"scopes": scopes, "detector": "full", "viewport": VIEWPORT})
    shots = await screenshots(members)
    results: dict[str, Any] = {"canary_members": len(members), "canaries": len(canaries)}
    with tempfile.TemporaryDirectory() as workdir:
        for scope in scopes:
            results[scope] = await measure(scope, shots, members, Path(workdir))
            figures = results[scope]
            print(
                f"{scope:9} {figures['screenshots']} screenshots, "
                f"{figures['canaries_readable']} canaries readable, "
                f"{figures['canaries_leaked']} leaked {figures['leaked_by_type']}"
            )
    run.write("screenshot_leak.json", results)
    tesseract = (await (await asyncio.create_subprocess_exec(
        "tesseract", "--version", stdout=asyncio.subprocess.PIPE
    )).communicate())[0].decode().splitlines()[0]  # fmt: skip
    run.finish(models={"ner": GLINER_MODEL, "ocr": tesseract})
    print(f"-> {run.directory}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analysis.screenshot_leak")
    parser.add_argument("--scopes", default="document,member")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.scopes.split(",")))


if __name__ == "__main__":
    sys.exit(main())
