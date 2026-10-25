"""Screenshot redaction: values rendered as pixels are found by OCR and painted over with their tokens."""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image, ImageDraw, ImageFont

from proxy.app import Services, create_app
from proxy.cassettes import IMAGE_REF, CassetteStore, externalize_images, request_digest
from proxy.detection import DetectionError
from proxy.offline import RecordingProvider
from proxy.vision import decode_data_url, encode_png, ocr_region, ocr_text, redact_image
from tests.proxy.fakes import MOCK_MODEL, kit, services

needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs tesseract-ocr")

VALUES = ["M1234567", "POL-2024-0042", "CLM-2025-104233"]


def render(lines: list[str]) -> bytes:
    """A plain form-like page, in the portal's font size range."""
    image = Image.new("RGB", (760, 40 + 34 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=16)
    for i, line in enumerate(lines):
        draw.text((24, 20 + 34 * i), line, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


PAGE = render(
    [
        "Reimbursement request",
        f"Member number: {VALUES[0]}",
        f"Policy {VALUES[1]} covers this claim",
        f"Claim {VALUES[2]} is under review",
    ]
)


@needs_tesseract
async def test_ocr_reads_the_rendered_page() -> None:
    text = await ocr_text(PAGE)
    assert all(value in text for value in VALUES)


@needs_tesseract
async def test_redaction_paints_tokens_over_every_value(tmp_path: Path) -> None:
    k = kit(tmp_path)
    redacted, painted = await redact_image(PAGE, k.masker, "t1", "local")
    text = await ocr_text(redacted)
    assert painted >= len(VALUES)
    assert not any(value in text for value in VALUES)
    assert "Reimbursement request" in text
    assert len(await k.vault.entries("t1")) == len(VALUES)


@needs_tesseract
async def test_known_values_are_painted_even_without_a_pattern(tmp_path: Path) -> None:
    k = kit(tmp_path)
    await k.masker.mask_value({"fullName": "Darlene Whitfield"}, "t1", "agent-state")
    redacted, _ = await redact_image(render(["Payee: Darlene Whitfield"]), k.masker, "t1", "local")
    assert "Whitfield" not in await ocr_text(redacted)


async def test_unreadable_images_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(DetectionError):
        await redact_image(b"not an image", kit(tmp_path).masker, "t1", "local")
    with pytest.raises(DetectionError):
        decode_data_url("https://example.com/page.png")


@needs_tesseract
async def test_the_proxy_forwards_only_the_redacted_screenshot(tmp_path: Path) -> None:
    upstream = RecordingProvider()
    built, _ = services(tmp_path, upstream)

    async def build() -> Services:
        return built

    app: FastAPI = create_app(build)
    app.state.services = built
    body = {
        "model": MOCK_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Fill in the form."},
                    {"type": "image_url", "image_url": {"url": encode_png(PAGE)}},
                ],
            }
        ],
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy") as client:
        response = await client.post("/v1/chat/completions", json=body, headers={"X-LB-Task-Id": "t1"})
    await built.http.aclose()
    assert response.status_code == 200
    sent = json.loads(upstream.bodies[-1])["messages"][0]["content"][1]["image_url"]["url"]
    text = await ocr_text(decode_data_url(sent))
    assert not any(value in text for value in VALUES)


def test_cassettes_store_images_beside_the_exchange(tmp_path: Path) -> None:
    request: dict[str, object] = {
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": encode_png(PAGE)}}]}
        ]
    }
    store = CassetteStore(tmp_path)
    exchange = store.save("t1", 0, request, "m", 200, False, "{}")
    url = exchange.request["messages"][0]["content"][0]["image_url"]["url"]  # type: ignore[index]
    assert url.startswith(IMAGE_REF)
    assert [p.name for p in (tmp_path / "t1").glob("*.png")] == [f"000-{url.removeprefix(IMAGE_REF)}.png"]
    assert (
        exchange.request_sha256
        == request_digest(request)
        == request_digest(
            externalize_images(request)  # type: ignore[arg-type]
        )
    )


@needs_tesseract
async def test_element_text_starting_at_the_box_edge_keeps_its_first_letter() -> None:
    image = Image.new("RGB", (400, 120), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=13)
    draw.text((100, 50), "Claim number", fill="black", font=font)
    box = (100, 47, 270, 69)  # the caption is drawn from the box's left edge, as the portal's canvases are
    assert await ocr_region(image, box) == "Claim number"
