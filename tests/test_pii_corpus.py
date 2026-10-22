from __future__ import annotations

import json

from analysis.pii_benchmark import Counts, score_document
from analysis.pii_corpus import GoldSpan, build_corpus
from proxy.detection import Span
from testbed.seed import generate

DS, TASKS, CANARIES = generate()
CANARY_MEMBERS = [m for m in DS.members if m.member_number in DS.canary_member_numbers]
DOCS = build_corpus(DS, TASKS, CANARY_MEMBERS, seed=1)


def test_gold_spans_are_in_bounds_and_disjoint() -> None:
    for doc in DOCS:
        last_end = 0
        for span in doc.gold:
            assert 0 <= last_end <= span.start < span.end <= len(doc.text), doc.id
            last_end = span.end


def test_json_surfaces_are_valid_json() -> None:
    for doc in DOCS:
        if doc.surface in {"api_json", "api_drift_json"}:
            json.loads(doc.text)


def test_every_canary_value_appears_in_the_corpus_as_gold() -> None:
    """The leak check is only meaningful if every canary is actually sent through the proxy."""
    gold_values = {doc.text[s.start : s.end] for doc in DOCS for s in doc.gold}
    assert {c.value for c in CANARIES} - gold_values == set()


def test_scoring_counts_iou_matches_one_to_one() -> None:
    gold = [GoldSpan(start=0, end=10, entity_type="PERSON"), GoldSpan(start=20, end=30, entity_type="PERSON")]
    predicted = [
        Span(start=1, end=10, entity_type="PERSON", score=1, source="t"),
        Span(start=2, end=9, entity_type="PERSON", score=1, source="t"),
        Span(start=40, end=45, entity_type="PERSON", score=1, source="t"),
    ]
    assert score_document(gold, predicted, {"PERSON"})["PERSON"] == Counts(tp=1, fp=2, fn=1)


def test_f1_is_zero_for_a_miss_without_predictions_and_undefined_without_data() -> None:
    assert Counts(tp=0, fp=0, fn=5).f1 == 0.0
    assert Counts(tp=0, fp=0, fn=0).f1 is None
    assert Counts(tp=3, fp=1, fn=1).f1 == 0.75
