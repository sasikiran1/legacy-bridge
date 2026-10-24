"""Prompt tokens a page snapshot costs, raw vs. trimmed (agent.executors.trim_snapshot).

    uv run python -m analysis.snapshot_trim

Measured on every distinct page observation in the committed cassettes (tokenized text, exactly what the
model was sent) with a real tokenizer. Also checks that trimming keeps every ref the model could act on.
Results hold counts only.
"""

from __future__ import annotations

import json
import re
import statistics
import sys

from transformers import AutoTokenizer

from agent.executors import ACTIONABLE, element_index, trim_snapshot
from analysis.manifest import Run
from analysis.token_overhead import TOKENIZERS
from proxy.cassettes import CASSETTE_DIR

_PAGE = re.compile(r'<untrusted_content source="portal-page">\n(.*?)\n</untrusted_content>', re.S)


def recorded_pages() -> list[str]:
    pages: dict[str, None] = {}
    for path in sorted(CASSETTE_DIR.glob("*/[0-9][0-9][0-9].json")):
        for message in json.loads(path.read_text(encoding="utf-8"))["request"]["messages"]:
            match = _PAGE.search(message.get("content") or "")
            if match:
                pages[match.group(1)] = None
    return list(pages)


def main(argv: list[str] | None = None) -> int:
    pages = recorded_pages()
    lost = sum(
        1
        for page in pages
        for ref, (role, _) in element_index(page).items()
        if role in ACTIONABLE and f"[ref={ref}]" not in trim_snapshot(page)
    )
    run = Run(kind="snapshot-trim", config={"pages": len(pages), "tokenizers": list(TOKENIZERS)})
    results: dict[str, object] = {"pages": len(pages), "actionable_refs_lost": lost}
    for name in TOKENIZERS:
        tokenizer = AutoTokenizer.from_pretrained(name)
        raw = [len(tokenizer.encode(p, add_special_tokens=False)) for p in pages]
        trimmed = [len(tokenizer.encode(trim_snapshot(p), add_special_tokens=False)) for p in pages]
        saved = [1 - t / r for r, t in zip(raw, trimmed, strict=True) if r]
        results[name] = {
            "raw_tokens_total": sum(raw),
            "trimmed_tokens_total": sum(trimmed),
            "reduction_total": round(1 - sum(trimmed) / sum(raw), 3),
            "median_reduction_per_page": round(statistics.median(saved), 3),
            "median_raw_tokens_per_page": statistics.median(raw),
            "median_trimmed_tokens_per_page": statistics.median(trimmed),
        }
        print(name, results[name])
    run.write("snapshot_trim.json", results)
    run.finish(models=dict.fromkeys(TOKENIZERS, "tokenizer only"))
    print(f"{len(pages)} pages, actionable refs lost: {lost} -> {run.directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
