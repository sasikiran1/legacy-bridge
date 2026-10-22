"""LLM-token cost of candidate token renderings (ADR 0001, ADR 0011), measured with a real tokenizer.

    uv run python -m analysis.token_overhead

Tokenizes the synthetic corpus raw and with every gold PII span replaced by each candidate rendering, so the
comparison isolates the format from detection quality. Only tokenizer files are downloaded.
"""

from __future__ import annotations

import statistics
import sys
import uuid
from collections.abc import Callable

from transformers import AutoTokenizer

from analysis.manifest import Run
from analysis.pii_corpus import build_corpus
from proxy.offline import SYNTHETIC_TOKEN_KEY
from proxy.tokens import TokenMinter
from testbed.seed import generate

TOKENIZERS = ("openai/gpt-oss-20b",)
MEMBERS = 40

Render = Callable[[str, uuid.UUID], str]
VARIANTS: dict[str, Render] = {
    "unicode_uuid": lambda t, u: f"⟦{t}:{u}⟧",
    "unicode_hex10": lambda t, u: f"⟦{t}:{u.hex[:10]}⟧",
    "ascii_hex10": lambda t, u: f"[[{t}:{u.hex[:10]}]]",
    "ascii_hex8": lambda t, u: f"[[{t}:{u.hex[:8]}]]",
}


def renderings(members: int) -> tuple[list[str], dict[str, list[tuple[str, list[str]]]]]:
    """Raw documents, and per variant: (document text, rendered tokens in it)."""
    ds, tasks, _ = generate()
    pool = [m for m in ds.members if m.member_number not in ds.canary_member_numbers][:members]
    minter = TokenMinter(SYNTHETIC_TOKEN_KEY, "7")
    raw: list[str] = []
    variants: dict[str, list[tuple[str, list[str]]]] = {name: [] for name in VARIANTS}
    for doc in build_corpus(ds, tasks, pool, seed=4):
        raw.append(doc.text)
        for name, render in VARIANTS.items():
            parts, tokens, cursor = [], [], 0
            for span in doc.gold:
                canonical = minter.mint(
                    doc.member_number, span.entity_type, doc.text[span.start : span.end]
                ).canonical
                token = render(span.entity_type, canonical)
                parts += [doc.text[cursor : span.start], token]
                tokens.append(token)
                cursor = span.end
            parts.append(doc.text[cursor:])
            variants[name].append(("".join(parts), tokens))
    return raw, variants


def main() -> int:
    run = Run(
        kind="token-overhead",
        config={"members": MEMBERS, "tokenizers": list(TOKENIZERS), "variants": list(VARIANTS)},
    )
    raw, variants = renderings(MEMBERS)
    results: dict[str, object] = {}
    for repo in TOKENIZERS:
        tokenizer = AutoTokenizer.from_pretrained(repo)

        def count(text: str, tokenizer: object = tokenizer) -> int:
            return len(tokenizer.encode(text, add_special_tokens=False))  # type: ignore[attr-defined]

        raw_total = sum(count(text) for text in raw)
        per_variant = {}
        for name, docs in variants.items():
            total = sum(count(text) for text, _ in docs)
            per_variant[name] = {
                "prompt_tokens": total,
                "prompt_change_vs_raw": total / raw_total - 1,
                "llm_tokens_per_pii_token": statistics.fmean(count(t) for _, tokens in docs for t in tokens),
            }
            print(
                f"{repo} {name:14} {per_variant[name]['llm_tokens_per_pii_token']:5.1f} tokens each, "
                f"prompt {per_variant[name]['prompt_change_vs_raw']:+.0%} vs raw"
            )
        results[repo] = {
            "documents": len(raw),
            "pii_spans": sum(len(tokens) for _, tokens in next(iter(variants.values()))),
            "raw_prompt_tokens": raw_total,
            "variants": per_variant,
        }
    run.write("token_overhead.json", results)
    run.finish(models={name: "tokenizer only" for name in TOKENIZERS})
    print(f"-> {run.directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
