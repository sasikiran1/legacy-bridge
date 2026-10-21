# 0008. Vision fallback model and screenshot redaction

- Status: Proposed. Decided in M4.
- Date: 2026-10-21

## Context

One portal page has a poor accessibility tree (unlabeled divs and a canvas), so accessibility snapshots can't drive it. That page needs a vision model. Screenshots contain rendered PII that text-level tokenization never saw.

## Starting point

- **Default model:** `qwen3-vl:8b` via Ollama.
  - Apache-2.0, 6.2 GB, fits the 12 GB GPU.
  - Its model card documents GUI-element understanding and OCR.
  - Stays local, so in principle it could see unredacted screenshots. We redact anyway, to keep one rule and measure the cost of redaction.
- **CI and low-memory profile:** `qwen3-vl:4b`.
- **Redaction pipeline** (runs before any non-local model, and by default before the local one too):
  1. Tesseract OCR (in the proxy image) produces word boxes.
  2. Line text goes through the same detector as text.
  3. Detected spans map back to boxes, which are filled black.
  4. A canary-rendering test asserts that no canary survives OCR of the redacted image.
- **Hosted VLM:** used only if the local model fails the vision tasks. It receives redacted images only.

## To decide with evidence

- Success rate of `qwen3-vl:8b` vs `:4b` on the poor-accessibility page.
- Whether redaction hurts task success: black boxes can hide the very field the agent must click. Mitigation: replace black boxes with typed placeholders (`[PERSON]`) drawn in the box.
- OCR miss rate on the portal's fonts, and whether a local OCR model beats Tesseract.
