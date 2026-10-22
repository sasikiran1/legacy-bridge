"""PII detectors. Four configurations, benchmarked against each other in analysis/pii_benchmark.py:

    regex             custom pattern rules + Presidio's pattern recognizers, no NLP
    presidio          Presidio's default AnalyzerEngine (spaCy en_core_web_lg + predefined recognizers)
    presidio_gliner   the above + the GLiNER PII model
    full              the above + custom rules, filtered by the PII policy

Detectors are synchronous and CPU-bound; the proxy runs them in a worker thread behind a lock.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict

from proxy.policy import PiiPolicy

if TYPE_CHECKING:
    from presidio_analyzer import AnalyzerEngine, EntityRecognizer


class DetectionError(Exception):
    """The detector failed. Callers must fail closed."""


class Span(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: int
    end: int
    entity_type: str
    score: float
    source: str


class Detector(Protocol):
    name: str

    def detect(self, text: str) -> list[Span]: ...


class DetectorConfig(StrEnum):
    REGEX = "regex"
    PRESIDIO = "presidio"
    PRESIDIO_GLINER = "presidio_gliner"
    FULL = "full"


PRESIDIO_TYPES = {
    "PERSON": "PERSON",
    "EMAIL_ADDRESS": "EMAIL_ADDRESS",
    "PHONE_NUMBER": "PHONE_NUMBER",
    "US_SSN": "US_SSN",
    "CREDIT_CARD": "CREDIT_CARD",
    "IBAN_CODE": "IBAN_CODE",
    "US_BANK_NUMBER": "BANK_ACCOUNT",
    "US_DRIVER_LICENSE": "DRIVER_LICENSE",
    "US_PASSPORT": "PASSPORT",
    "LOCATION": "LOCATION",
    "DATE_TIME": "DATE_TIME",
}
"""Presidio entity names to ours. Types outside the PII policy are dropped by the `full` configuration."""

GLINER_LABELS = {
    "person": "PERSON",
    "email": "EMAIL_ADDRESS",
    "phone number": "PHONE_NUMBER",
    "social security number": "US_SSN",
    "street address": "STREET_ADDRESS",
    "date of birth": "DATE_OF_BIRTH",
    "bank account number": "BANK_ACCOUNT",
    "credit card number": "CREDIT_CARD",
    "passport number": "PASSPORT",
    "driver's license number": "DRIVER_LICENSE",
}
GLINER_MODEL = "urchade/gliner_multi_pii-v1"


# --- custom pattern rules


def aba_checksum(digits: str) -> bool:
    weights = (3, 7, 1, 3, 7, 1, 3, 7, 1)
    return len(digits) == 9 and sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 10 == 0


_SUFFIXES = [
    line.strip()
    for line in (Path(__file__).parent / "data" / "usps_street_suffixes.txt")
    .read_text(encoding="utf-8")
    .splitlines()
    if line.strip() and not line.startswith("#")
]

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_DATE = rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}/\d{{4}}|{_MONTH} \d{{1,2}}, \d{{4}})"


@dataclass(frozen=True)
class PatternRule:
    entity_type: str
    pattern: re.Pattern[str]
    score: float
    context: tuple[str, ...] = ()
    context_required: bool = False
    window: int = 40
    validate: Callable[[str], bool] | None = None

    def find(self, text: str) -> Iterable[Span]:
        lowered = text.lower()
        for match in self.pattern.finditer(text):
            start, end = match.span()
            if self.validate and not self.validate(match.group()):
                continue
            before = lowered[max(0, start - self.window) : start]
            has_context = any(word in before for word in self.context)
            if self.context_required and not has_context:
                continue
            boost = 0.35 if has_context and not self.context_required else 0.0
            yield Span(
                start=start,
                end=end,
                entity_type=self.entity_type,
                score=min(1.0, self.score + boost),
                source=f"rule:{self.entity_type}",
            )


CUSTOM_RULES: tuple[PatternRule, ...] = (
    PatternRule("MEMBER_ID", re.compile(r"\bM\d{7}\b"), 0.85),
    PatternRule("POLICY_NUMBER", re.compile(r"\bPOL-\d{4}-\d{4}\b"), 0.9),
    PatternRule("CLAIM_NUMBER", re.compile(r"\bCLM-\d{4}-\d{6}\b"), 0.9),
    PatternRule(
        "US_SSN",
        re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
        0.6,
        context=("ssn", "social security", "social"),
    ),
    PatternRule(
        "ROUTING_NUMBER",
        re.compile(r"\b\d{9}\b"),
        0.3,
        context=("routing", "aba", "transit"),
        validate=aba_checksum,
    ),
    PatternRule(
        "BANK_ACCOUNT",
        re.compile(r"\b\d{6,17}\b"),
        0.7,
        context=("account number", "account no", "acct", "bankaccount", "bank account", "account"),
        context_required=True,
        window=25,
    ),
    PatternRule(
        "DATE_OF_BIRTH",
        re.compile(rf"\b{_DATE}\b"),
        0.85,
        context=("birth", "dob", "d.o.b", "born"),
        context_required=True,
    ),
    PatternRule(
        "STREET_ADDRESS",
        re.compile(
            r"\b\d{1,6}(?:\s+[A-Z][A-Za-z'.-]*){1,4}\s+(?:" + "|".join(_SUFFIXES) + r")\b\.?"
            r"(?:,?\s+(?:Apt\.?|Suite|Unit|#)\s*[\w-]+)?"
        ),
        0.75,
    ),
)


# --- overlap resolution


_STREET_RULE = next(rule for rule in CUSTOM_RULES if rule.entity_type == "STREET_ADDRESS")
_CITY_STATE_ZIP = re.compile(r"[A-Za-z .'-]+,\s*[A-Z]{2}\s+\d{5}(-\d{4})?")


NUMERIC_TYPES = frozenset(
    {
        "DATE_OF_BIRTH",
        "US_SSN",
        "PHONE_NUMBER",
        "BANK_ACCOUNT",
        "ROUTING_NUMBER",
        "CREDIT_CARD",
        "IBAN_CODE",
        "MEMBER_ID",
        "POLICY_NUMBER",
        "CLAIM_NUMBER",
    }
)


ROLE_WORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "our",
        "your",
        "their",
        "user",
        "users",
        "administrator",
        "admin",
        "developer",
        "system",
        "member",
        "members",
        "clerk",
        "staff",
        "operator",
        "customer",
        "agent",
        "assistant",
        "supervisor",
        "manager",
        "reviewer",
        "payee",
        "patient",
        "name",
        "names",
        "full",
    ]
)


def plausible(span: Span, text: str) -> bool:
    """Numeric entity types need at least two digits: NER tags column headers such as "Date of birth" or
    "SSN" as the entities they describe. A PERSON span made only of role nouns ("the user", "an
    administrator") is not a name; GLiNER scores those above 0.75 in some sentences."""
    value = text[span.start : span.end]
    if span.entity_type == "PERSON":
        return not all(word.strip(".,;:'\"()").lower() in ROLE_WORDS for word in value.split())
    if span.entity_type not in NUMERIC_TYPES:
        return True
    return sum(ch.isdigit() for ch in value) >= 2


def trim_street_spans(spans: Sequence[Span], text: str) -> list[Span]:
    """Cuts a street-address span at its first comma when the part before is a complete street address, and
    drops spans that are only "City, ST 12345".

    NER often tags "12 Elm St, Springfield, IL 62701" as one address, or tags the city part on its own. City,
    state and ZIP are not tokenized by policy, and folding them into street tokens leaves the agent unable to
    fill separate form fields.
    """
    trimmed = []
    for span in spans:
        if span.entity_type == "STREET_ADDRESS":
            value = text[span.start : span.end]
            if _CITY_STATE_ZIP.fullmatch(value.strip()):
                continue
            comma = value.find(",")
            if comma > 0 and _STREET_RULE.pattern.fullmatch(value[:comma].rstrip()):
                span = span.model_copy(update={"end": span.start + len(value[:comma].rstrip())})
        trimmed.append(span)
    return trimmed


def resolve_overlaps(spans: Sequence[Span]) -> list[Span]:
    """Union overlapping same-type spans; a span inside a longer span yields to it; then partial overlaps
    keep the highest-scoring span. Containment wins over score because masking part of a value (the name
    inside a street address) leaves the rest of it in clear."""
    by_type: dict[str, list[Span]] = {}
    for span in sorted(spans, key=lambda s: (s.entity_type, s.start)):
        group = by_type.setdefault(span.entity_type, [])
        if group and span.start < group[-1].end:
            last = group[-1]
            group[-1] = Span(
                start=last.start,
                end=max(last.end, span.end),
                entity_type=last.entity_type,
                score=max(last.score, span.score),
                source=last.source if last.score >= span.score else span.source,
            )
        else:
            group.append(span)
    merged = [s for group in by_type.values() for s in group]
    outer = [
        s
        for s in merged
        if not any(
            o.start <= s.start and s.end <= o.end and o.end - o.start > s.end - s.start for o in merged
        )
    ]
    chosen: list[Span] = []
    for span in sorted(outer, key=lambda s: (-s.score, -(s.end - s.start), s.start)):
        if all(span.end <= c.start or span.start >= c.end for c in chosen):
            chosen.append(span)
    return sorted(chosen, key=lambda s: s.start)


# --- detectors


def _presidio_pattern_recognizers() -> list[EntityRecognizer]:
    from presidio_analyzer.predefined_recognizers import (
        CreditCardRecognizer,
        EmailRecognizer,
        IbanRecognizer,
        PhoneRecognizer,
        UsSsnRecognizer,
    )

    return [EmailRecognizer(), PhoneRecognizer(), UsSsnRecognizer(), CreditCardRecognizer(), IbanRecognizer()]


class RegexDetector:
    name = DetectorConfig.REGEX.value

    def __init__(
        self, rules: Sequence[PatternRule] = CUSTOM_RULES, with_presidio_patterns: bool = True
    ) -> None:
        self._rules = tuple(rules)
        self._recognizers = _presidio_pattern_recognizers() if with_presidio_patterns else []

    def detect(self, text: str) -> list[Span]:
        spans = [span for rule in self._rules for span in rule.find(text)]
        for recognizer in self._recognizers:
            for result in recognizer.analyze(text, recognizer.supported_entities, None):  # type: ignore[arg-type]
                mapped = PRESIDIO_TYPES.get(result.entity_type)
                if mapped:
                    spans.append(
                        Span(
                            start=result.start,
                            end=result.end,
                            entity_type=mapped,
                            score=result.score,
                            source=f"presidio:{type(recognizer).__name__}",
                        )
                    )
        return trim_street_spans(resolve_overlaps(spans), text)


class PresidioDetector:
    def __init__(
        self,
        with_gliner: bool,
        rules: Sequence[PatternRule] = (),
        drop_spacy_ner: bool = False,
        policy: PiiPolicy | None = None,
        name: str = DetectorConfig.PRESIDIO.value,
    ) -> None:
        self.name = name
        self._rules = tuple(rules)
        self._policy = policy
        self._lock = threading.Lock()
        self._engine = self._build(with_gliner, drop_spacy_ner)

    @staticmethod
    def _build(with_gliner: bool, drop_spacy_ner: bool) -> AnalyzerEngine:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.predefined_recognizers import GLiNERRecognizer

        engine = AnalyzerEngine()
        if with_gliner:
            engine.registry.add_recognizer(
                GLiNERRecognizer(
                    entity_mapping=GLINER_LABELS,
                    model_name=GLINER_MODEL,
                    flat_ner=True,
                    multi_label=False,
                    threshold=0.3,
                    map_location="cpu",
                )
            )
        if drop_spacy_ner:
            # spaCy's NER tags UI labels such as "Mailing Address" as people; with GLiNER present it adds
            # false positives rather than recall (measured in analysis/results/*-pii-benchmark).
            engine.registry.remove_recognizer("SpacyRecognizer")
        return engine

    def detect(self, text: str) -> list[Span]:
        try:
            with self._lock:
                results = self._engine.analyze(text=text, language="en")
        except Exception as e:
            raise DetectionError(f"{self.name} detector failed: {e}") from e
        spans = [span for rule in self._rules for span in rule.find(text)]
        for result in results:
            mapped = result.entity_type if result.entity_type in GLINER_LABELS.values() else None
            mapped = mapped or PRESIDIO_TYPES.get(result.entity_type)
            if mapped:
                spans.append(
                    Span(
                        start=result.start,
                        end=result.end,
                        entity_type=mapped,
                        score=result.score,
                        source="presidio:"
                        + str((result.recognition_metadata or {}).get("recognizer_name", "")),
                    )
                )
        if self._policy is not None:
            policy = self._policy
            spans = [
                s
                for s in spans
                if policy.tokenizes(s.entity_type)
                and s.score >= policy.min_score(s.entity_type)
                and plausible(s, text)
            ]
        return trim_street_spans(resolve_overlaps(spans), text)


def build_detector(config: DetectorConfig, policy: PiiPolicy) -> Detector:
    match config:
        case DetectorConfig.REGEX:
            return RegexDetector()
        case DetectorConfig.PRESIDIO:
            return PresidioDetector(with_gliner=False, name=config.value)
        case DetectorConfig.PRESIDIO_GLINER:
            return PresidioDetector(with_gliner=True, name=config.value)
        case DetectorConfig.FULL:
            return PresidioDetector(
                with_gliner=True, rules=CUSTOM_RULES, policy=policy, drop_spacy_ner=True, name=config.value
            )
