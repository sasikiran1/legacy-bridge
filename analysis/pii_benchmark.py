"""PII detection benchmark: precision, recall and F1 per entity type, for each detector configuration.

    uv run python -m analysis.pii_benchmark [--synthetic-members 60] [--ai4privacy-rows 600]

Datasets:
    synthetic   portal documents (analysis/pii_corpus.py) for non-canary members; gold spans are exact
    ai4privacy  English rows of the open-pii-masking-500k validation split, labels mapped to our types.
                Evaluation only: downloaded to analysis/data/raw (gitignored); rows are never committed or
                published, only these aggregates. Built with Llama; see docs/environment.md for terms.

Matching: a prediction matches a gold span of the same type when their character IoU is at least 0.5
(one-to-one, greedy by IoU). Predictions of types a dataset has no gold for are ignored for P/R/F1 and counted
separately. `char_recall` is the share of gold PII characters covered by any prediction of any type, which is
what matters for leakage.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from analysis.manifest import REPO_ROOT, Run
from analysis.pii_corpus import Document, GoldSpan, build_corpus
from proxy.detection import GLINER_MODEL, DetectorConfig, Span, build_detector
from proxy.policy import load_policy
from testbed.seed import generate

AI4PRIVACY_REPO = "ai4privacy/open-pii-masking-500k-ai4privacy"
AI4PRIVACY_FILE = "data/validation/test.jsonl"
RAW_DIR = REPO_ROOT / "analysis" / "data" / "raw" / "ai4privacy"

AI4PRIVACY_LABELS = {
    "GIVENNAME": "PERSON",
    "SURNAME": "PERSON",
    "EMAIL": "EMAIL_ADDRESS",
    "TELEPHONENUM": "PHONE_NUMBER",
    "SOCIALNUM": "US_SSN",
    "STREET": "STREET_ADDRESS",
    "BUILDINGNUM": "STREET_ADDRESS",
    "CREDITCARDNUMBER": "CREDIT_CARD",
}


class Counts(BaseModel):
    model_config = ConfigDict(frozen=True)

    tp: int
    fp: int
    fn: int

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def f1(self) -> float | None:
        """Undefined only when there is neither gold nor prediction; a miss with no predictions scores 0."""
        if self.tp + self.fp + self.fn == 0:
            return None
        return 2 * self.tp / (2 * self.tp + self.fp + self.fn)

    def as_dict(self) -> dict[str, object]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
        }


def _iou(a: Span | GoldSpan, b: Span | GoldSpan) -> float:
    overlap = min(a.end, b.end) - max(a.start, b.start)
    if overlap <= 0:
        return 0.0
    return overlap / (max(a.end, b.end) - min(a.start, b.start))


def score_document(gold: Sequence[GoldSpan], predicted: Sequence[Span], types: set[str]) -> dict[str, Counts]:
    counts: dict[str, Counts] = {}
    for entity_type in types:
        g = [s for s in gold if s.entity_type == entity_type]
        p = [s for s in predicted if s.entity_type == entity_type]
        pairs = sorted(((_iou(a, b), i, j) for i, a in enumerate(g) for j, b in enumerate(p)), reverse=True)
        used_g, used_p = set(), set()
        for iou, i, j in pairs:
            if iou >= 0.5 and i not in used_g and j not in used_p:
                used_g.add(i)
                used_p.add(j)
        counts[entity_type] = Counts(tp=len(used_g), fp=len(p) - len(used_p), fn=len(g) - len(used_g))
    return counts


def char_coverage(gold: Sequence[GoldSpan], predicted: Sequence[Span]) -> tuple[int, int]:
    covered: set[int] = set()
    for span in predicted:
        covered.update(range(span.start, span.end))
    gold_chars = {i for span in gold for i in range(span.start, span.end)}
    return len(gold_chars & covered), len(gold_chars)


def ai4privacy_documents(rows: int) -> list[Document]:
    from huggingface_hub import hf_hub_download

    path = Path(hf_hub_download(AI4PRIVACY_REPO, AI4PRIVACY_FILE, repo_type="dataset", local_dir=RAW_DIR))
    documents: list[Document] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record.get("language") != "en":
                continue
            text = record["source_text"]
            spans = sorted(
                (
                    GoldSpan(start=m["start"], end=m["end"], entity_type=AI4PRIVACY_LABELS[m["label"]])
                    for m in record["privacy_mask"]
                    if m["label"] in AI4PRIVACY_LABELS
                ),
                key=lambda s: s.start,
            )
            documents.append(
                Document(
                    id=str(record["uid"]),
                    surface="ai4privacy",
                    member_number="",
                    text=text,
                    gold=tuple(_merge_adjacent(spans, text)),
                )
            )
            if len(documents) >= rows:
                break
    return documents


def _merge_adjacent(spans: Iterable[GoldSpan], text: str) -> list[GoldSpan]:
    """Given name + surname, or building number + street, become one span, as our detectors report them."""
    merged: list[GoldSpan] = []
    for span in spans:
        if (
            merged
            and merged[-1].entity_type == span.entity_type
            and re.fullmatch(r"[\s,]{0,2}", text[merged[-1].end : span.start])
        ):
            merged[-1] = GoldSpan(start=merged[-1].start, end=span.end, entity_type=span.entity_type)
        else:
            merged.append(span)
    return merged


def evaluate(config: DetectorConfig, datasets: dict[str, list[Document]]) -> dict[str, object]:
    detector = build_detector(config, load_policy())
    result: dict[str, object] = {}
    for name, documents in datasets.items():
        types = {g.entity_type for d in documents for g in d.gold}
        totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        covered = total_chars = other_type = chars = 0
        seconds = 0.0
        for doc in documents:
            started = time.perf_counter()
            predicted = detector.detect(doc.text)
            seconds += time.perf_counter() - started
            chars += len(doc.text)
            other_type += sum(1 for p in predicted if p.entity_type not in types)
            for entity_type, c in score_document(doc.gold, predicted, types).items():
                totals[entity_type][0] += c.tp
                totals[entity_type][1] += c.fp
                totals[entity_type][2] += c.fn
            hit, all_chars = char_coverage(doc.gold, predicted)
            covered, total_chars = covered + hit, total_chars + all_chars
        per_type = {t: Counts(tp=v[0], fp=v[1], fn=v[2]) for t, v in sorted(totals.items())}
        micro = Counts(
            tp=sum(c.tp for c in per_type.values()),
            fp=sum(c.fp for c in per_type.values()),
            fn=sum(c.fn for c in per_type.values()),
        )
        result[name] = {
            "documents": len(documents),
            "gold_spans": sum(len(d.gold) for d in documents),
            "per_type": {t: c.as_dict() for t, c in per_type.items()},
            "micro": micro.as_dict(),
            "char_recall": covered / total_chars if total_chars else None,
            "other_type_predictions": other_type,
            "ms_per_1k_chars": 1000 * seconds / (chars / 1000) if chars else None,
        }
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analysis.pii_benchmark")
    parser.add_argument("--synthetic-members", type=int, default=60)
    parser.add_argument("--ai4privacy-rows", type=int, default=600)
    parser.add_argument("--configs", default=",".join(c.value for c in DetectorConfig))
    args = parser.parse_args(argv)

    ds, tasks, _ = generate()
    pool = [m for m in ds.members if m.member_number not in ds.canary_member_numbers]
    datasets = {"synthetic": build_corpus(ds, tasks, pool[: args.synthetic_members], seed=2)}
    if args.ai4privacy_rows:
        datasets["ai4privacy"] = ai4privacy_documents(args.ai4privacy_rows)

    run = Run(
        kind="pii-benchmark",
        config={
            "synthetic_members": args.synthetic_members,
            "ai4privacy_rows": args.ai4privacy_rows,
            "ai4privacy_file": f"{AI4PRIVACY_REPO}/{AI4PRIVACY_FILE}",
            "match": "same type, char IoU >= 0.5",
        },
    )
    results = {}
    for name in args.configs.split(","):
        config = DetectorConfig(name)
        results[config.value] = evaluate(config, datasets)
        micro = {d: r["micro"] for d, r in results[config.value].items()}  # type: ignore[index]
        print(config.value, json.dumps(micro))
    run.write("pii_benchmark.json", results)
    run.finish(models={"gliner": GLINER_MODEL, "spacy": "en_core_web_lg-3.8.0"})
    print(f"-> {run.directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
