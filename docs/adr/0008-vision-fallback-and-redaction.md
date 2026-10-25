# 0008. Vision fallback model and screenshot redaction

- Status: Accepted (2026-10-25). Proposed 2026-10-21, decided in M4. The decision differs from the starting point: OCR feeds the text model, and no vision model is in the loop yet.
- Date: 2026-10-21

## Context

One portal page has a poor accessibility tree, so accessibility snapshots can't drive it. The `poor_a11y` reimbursement form has:

- six unnamed text boxes
- captions and buttons painted on canvases

That page needs something that reads pixels. Screenshots also contain rendered PII that text-level tokenization never saw.

## Starting point (2026-10-21)

- **Default model:** `qwen3-vl:8b` via Ollama, Apache-2.0, 6.2 GB. `qwen3-vl:4b` for CI and low-memory machines.
- **Redaction before any model:** Tesseract OCR word boxes, the same detector as text, and black boxes over detected spans. A canary-rendering test asserts that no canary survives.
- **Open questions:** success of 8b against 4b, whether redaction hurts task success, and the OCR miss rate.

## Decision

1. **Screenshots are redacted in the proxy, for every model, local or not** (`proxy/vision.py`).
   - Each image part is OCR'd with Tesseract 5.3.4 at twice its size.
   - Each OCR line is masked like any text: detector plus the task's known values.
   - Every masked value is painted over with its token rather than a black box, so a model sees `[[PERSON:…]]` where the page showed a name.
   - A second OCR pass catches values the first one split.
   - Undecodable images and remote image URLs fail closed.
   - Cassettes store each redacted screenshot beside the exchange as a PNG, and the leak gate OCRs every stored screenshot for canaries.
2. **The agent reads the unlabeled page with OCR, not with a vision model.**
   - When the accessibility tree leaves inputs unnamed or paints text on a canvas, the executor locates those elements from the DOM and reads each canvas by OCR of its own crop.
   - It lists them for the text model with their positions. The model gets `click_at` and `type_at` only while such a list is present.
   - Both tools act only on a listed element, never on an arbitrary point.
3. **The label an input gets is the caption a person reads beside or above it on screen.**
   - The data policy matches on that label, exactly as it matches on accessible names (ADR 0003).
   - Coordinate clicks go through the same approval checks as ref clicks: a canvas "Confirm submission" button needs the payment approval.

## Evidence

- **Redaction, on real portal screenshots:** member pages of the 10 canary members, full detector (`analysis/results/20261025T000432Z-screenshot-leak/`).
  - OCR reads 100 canary values on the raw screenshots.
  - 0 survive redaction, both with the detector alone and with the API record's known values.
- **The agent on the poor-accessibility form:** `submit_reimbursement-01` passed with gemma4 through the unlabeled-element tools (13 model calls, 0 wrong actions). The run was stopped by request after that task, so this is one task, not a success rate.
- **The first attempt failed for an OCR reason, not a model reason.**
  - Cropping 2 px inside each canvas clipped the first letter of captions drawn at its edge, so "Claim number" read as "Slaim number" (4 of the 7 form labels misread).
  - The data policy then correctly refused to type a claim number into that field, and the model stalled.
  - OCR of the whole box at 4× with a white margin reads all 7 labels.
  - The policy failing closed is the intended behaviour; the label read was the bug.

## Alternatives

- **A vision model choosing coordinates from the screenshot** (the starting point). Not chosen yet.
  - The text model plus an OCR'd element list solved the one task tried, with no extra model to download or keep resident beside gemma4 on the 12 GB GPU.
  - The proxy already redacts screenshots, so this arm can be added and measured without new PII machinery.
- **Black boxes instead of tokens.** Rejected: a model can't refer to a value it can't name, and a token keeps the text and image views of the page consistent.
- **Whole-page OCR for element text.** Rejected: Tesseract missed the bordered canvas button entirely; per-element crops read it.

## Consequences

- A label read by OCR can be wrong. A misread fails closed: the policy refuses the value, and the task stalls or hands off. It never types into the wrong field.
- **Two OCR blind spots remain.**
  - A value OCR cannot read on the raw screenshot also cannot be redacted. That leak path is not measured: the canary measure only counts values OCR reads.
  - Values painted in tiny or stylised text may be missed.
- **Open:**
  - the success rate over all seven poor-accessibility tasks
  - a vision-model arm (gemma4's own vision or `qwen3-vl:8b`) on the same tasks
  - whether token overlays hurt a vision model
