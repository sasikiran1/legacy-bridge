"""Screenshot redaction (ADR 0008): OCR the image, mask the recognised text like any other text, and paint
each masked value over with its token, so a vision model sees `[[PERSON:…]]` where the page showed a name.

Tesseract runs as a subprocess (the `tesseract-ocr` package in the Python image). Fails closed: an image
that cannot be decoded or read is a DetectionError, and the request is not forwarded.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from proxy.detection import DetectionError
from proxy.masking import Masker

UPSCALE = 2
"""Tesseract reads 13 px interface text far better at twice the size; boxes are scaled back afterwards."""

PASSES = 2
"""A second OCR pass over the redacted image catches values the first pass split or misread."""

MIN_CONFIDENCE = 30
PAD = 2
MIN_FONT = 9

_DATA_URL = re.compile(r"data:image/(?:png|jpeg|webp);base64,(?P<data>[A-Za-z0-9+/=\s]+)")


@dataclass(frozen=True)
class Word:
    text: str
    left: int
    top: int
    right: int
    bottom: int


@dataclass(frozen=True)
class Line:
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    def words_in(self, start: int, end: int) -> list[Word]:
        """Words overlapping the character range [start, end) of `text`."""
        found, cursor = [], 0
        for word in self.words:
            if cursor < end and start < cursor + len(word.text):
                found.append(word)
            cursor += len(word.text) + 1
        return found


def decode_data_url(url: str) -> bytes:
    match = _DATA_URL.fullmatch(url)
    if not match:
        raise DetectionError("only base64 PNG, JPEG or WebP data URLs can be redacted")
    try:
        return base64.b64decode(match["data"], validate=False)
    except binascii.Error as e:
        raise DetectionError(f"undecodable image data: {e}") from e


def encode_png(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _open(image: bytes) -> Image.Image:
    try:
        opened = Image.open(io.BytesIO(image))
        opened.load()
    except (UnidentifiedImageError, OSError) as e:
        raise DetectionError(f"unreadable image: {e}") from e
    return opened.convert("RGB")


async def ocr_lines(image: Image.Image) -> list[Line]:
    """Word boxes grouped into lines, in the image's own pixel coordinates."""
    scaled = image.resize((image.width * UPSCALE, image.height * UPSCALE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    scaled.save(buffer, format="PNG")
    try:
        process = await asyncio.create_subprocess_exec(
            "tesseract", "stdin", "stdout", "--psm", "11", "tsv",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
    except OSError as e:
        raise DetectionError(f"tesseract unavailable: {e}") from e
    out, err = await process.communicate(buffer.getvalue())
    if process.returncode != 0:
        raise DetectionError(f"tesseract failed: {err.decode(errors='replace')[:200]}")
    lines: dict[tuple[str, str, str], list[Word]] = {}
    for row in out.decode(errors="replace").splitlines()[1:]:
        cells = row.split("\t")
        if len(cells) != 12 or cells[0] != "5" or not cells[11].strip():
            continue
        if float(cells[10]) < MIN_CONFIDENCE:
            continue
        left, top, width, height = (int(c) // UPSCALE for c in cells[6:10])
        word = Word(cells[11].strip(), left, top, left + max(width, 1), top + max(height, 1))
        lines.setdefault((cells[2], cells[3], cells[4]), []).append(word)
    return [Line(tuple(words)) for words in lines.values()]


async def ocr_region(image: Image.Image, box: tuple[int, int, int, int]) -> str:
    """The text of one element (a canvas caption or button), read from its own crop as a single line.
    Whole-page OCR misses text inside bordered boxes; a tight crop does not."""
    left, top, right, bottom = box
    crop = image.crop((left + PAD, top + PAD, right - PAD, bottom - PAD))
    if crop.width < 4 or crop.height < 4:
        return ""
    scale = 3
    scaled = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    scaled.save(buffer, format="PNG")
    try:
        process = await asyncio.create_subprocess_exec(
            "tesseract", "stdin", "stdout", "--psm", "7",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
    except OSError as e:
        raise DetectionError(f"tesseract unavailable: {e}") from e
    out, _ = await process.communicate(buffer.getvalue())
    return " ".join(out.decode(errors="replace").split())


def open_image(image: bytes) -> Image.Image:
    return _open(image)


async def ocr_text(image: bytes) -> str:
    """All recognised text, one line per OCR line: what the leak test scans redacted screenshots for."""
    return "\n".join(line.text for line in await ocr_lines(_open(image)))


def _paint(draw: ImageDraw.ImageDraw, words: Sequence[Word], token: str) -> None:
    left = min(w.left for w in words) - PAD
    top = min(w.top for w in words) - PAD
    bottom = max(w.bottom for w in words) + PAD
    right = max(w.right for w in words) + PAD
    font = ImageFont.load_default(size=max(MIN_FONT, int((bottom - top) * 0.7)))
    # The token may be wider than the value it replaces; covering a little of the neighbouring text is the
    # safe direction.
    right = max(right, left + int(draw.textlength(token, font=font)) + 2 * PAD)
    draw.rectangle((left, top, right, bottom), fill="black")
    draw.text((left + PAD, top + PAD), token, fill="white", font=font)


async def redact_image(image: bytes, masker: Masker, task_id: str, destination: str) -> tuple[bytes, int]:
    """The image with every detected value painted over by its token, and how many values were painted."""
    picture = _open(image)
    painted = 0
    for _ in range(PASSES):
        draw = ImageDraw.Draw(picture)
        found = 0
        for line in await ocr_lines(picture):
            for start, end, token in await masker.tokenize_spans(line.text, task_id, destination):
                words = line.words_in(start, end)
                if words:
                    _paint(draw, words, token)
                    found += 1
        painted += found
        if not found:
            break
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue(), painted
