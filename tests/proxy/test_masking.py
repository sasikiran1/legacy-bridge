from __future__ import annotations

import json
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from proxy.detection import Span
from proxy.tokens import TOKEN_RE
from tests.proxy.fakes import kit, tokens_in

TEXT = (
    "Member M1234567 (DOB 03/02/1987) filed claim CLM-2025-104233 under POL-1234-5678. "
    "SSN 123-45-6789. Pay to routing number 021000021, account number 4400917733."
)
RAW_VALUES = [
    "M1234567",
    "03/02/1987",
    "CLM-2025-104233",
    "POL-1234-5678",
    "123-45-6789",
    "021000021",
    "4400917733",
]


async def test_mask_then_rehydrate_restores_the_exact_text(tmp_path: Path) -> None:
    k = kit(tmp_path)
    masked = await k.masker.mask_text(TEXT, "t1", "groq")
    assert not any(value in masked for value in RAW_VALUES)
    assert len(tokens_in(masked)) == len(RAW_VALUES)
    assert await k.rehydrator.text(masked, "t1", "client") == TEXT


async def test_same_value_gets_the_same_token_within_a_task(tmp_path: Path) -> None:
    k = kit(tmp_path)
    first = await k.masker.mask_text("Member M1234567 called.", "t1", "groq")
    second = await k.masker.mask_text("Again: M1234567.", "t1", "groq")
    other_task = await k.masker.mask_text("Again: M1234567.", "t2", "groq")
    assert tokens_in(first) == tokens_in(second)
    assert tokens_in(first) != tokens_in(other_task)


async def test_existing_tokens_are_left_alone(tmp_path: Path) -> None:
    k = kit(tmp_path)
    masked = await k.masker.mask_text("Member M1234567.", "t1", "groq")
    assert await k.masker.mask_text(masked, "t1", "groq") == masked


async def test_json_fields_are_masked_by_key_without_detection(tmp_path: Path) -> None:
    k = kit(tmp_path)
    document = json.dumps(
        {"firstName": "Ada", "lastName": "Lovelace", "product": "Dental Care", "city": "Albany"}
    )
    masked = json.loads(await k.masker.mask_content(document, "t1", "groq"))
    assert TOKEN_RE.fullmatch(masked["firstName"])
    assert TOKEN_RE.fullmatch(masked["lastName"])
    assert masked["product"] == "Dental Care"
    assert masked["city"] == "Albany"


async def test_values_seen_earlier_are_masked_in_free_text(tmp_path: Path) -> None:
    """The regex detector cannot find names; the known-value pass catches a name tokenized earlier."""
    k = kit(tmp_path)
    await k.masker.mask_content(json.dumps({"lastName": "Lovelace"}), "t1", "groq")
    masked = await k.masker.mask_text("Please call Mrs Lovelace back.", "t1", "groq")
    assert "Lovelace" not in masked


async def test_audit_never_contains_raw_values(tmp_path: Path) -> None:
    k = kit(tmp_path)
    masked = await k.masker.mask_text(TEXT, "t1", "groq")
    await k.rehydrator.text(masked, "t1", "client")
    log = k.audit.path.read_text(encoding="utf-8")
    assert not any(value in log for value in RAW_VALUES)
    assert log.count('"action":"mask"') == len(RAW_VALUES)


_PII = st.sampled_from([*RAW_VALUES, "M7654321", "POL-9999-0001", "987-65-4320"])
_FILLER = st.text(alphabet=st.characters(codec="utf-8", exclude_characters="[]"), max_size=20).filter(
    lambda s: not s or (s[0] == " " and s[-1] == " ")
)


@settings(max_examples=150, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(parts=st.lists(st.one_of(_PII, _FILLER), max_size=12))
async def test_round_trip_is_exact_for_arbitrary_compositions(tmp_path: Path, parts: list[str]) -> None:
    k = kit(tmp_path)
    text = " ".join(parts)
    masked = await k.masker.mask_text(text, "prop", "groq")
    assert await k.rehydrator.text(masked, "prop", "client") == text


class _GuessingDetector:
    """Mistakes "Member" for a person, but only in a sentence containing "called"."""

    name = "guess"

    def detect(self, text: str) -> list[Span]:
        if "called" not in text:
            return []
        start = text.find("Member")
        return [
            Span(
                start=start,
                end=start + 6,
                entity_type="PERSON",
                score=0.9,
                source="presidio:GLiNERRecognizer",
            )
        ]


async def test_single_word_ner_guesses_are_masked_but_not_spread(tmp_path: Path) -> None:
    k = kit(tmp_path, detector=_GuessingDetector())
    assert "Member" not in await k.masker.mask_text("Member called.", "t1", "groq")
    assert await k.masker.mask_text("Member Services Portal", "t1", "groq") == "Member Services Portal"


class _AccountOnlyInInstruction:
    """NER finds the account number beside its usual wording, as GLiNER did, not beside a drifted label."""

    name = "account-guesser"

    def detect(self, text: str) -> list[Span]:
        start = text.find("440091773312")
        if start == -1 or "account number" not in text:
            return []
        return [
            Span(start=start, end=start + 12, entity_type="BANK_ACCOUNT", score=0.9, source="presidio:GLiNER")
        ]


async def test_a_long_number_from_ner_is_caught_beside_a_drifted_label(tmp_path: Path) -> None:
    """Regression: a canary bank account reached a recorded prompt from a DOM-drift review page."""
    k = kit(tmp_path, detector=_AccountOnlyInInstruction())
    await k.masker.mask_text("Pay to bank account number 440091773312.", "t1", "groq")
    page = await k.masker.mask_text('- rowheader "Account no."\n- cell "440091773312"', "t1", "groq")
    assert "440091773312" not in page
