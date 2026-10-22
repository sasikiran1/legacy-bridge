from __future__ import annotations

import pytest
from pydantic import ValidationError

from proxy.detection import Span, resolve_overlaps, trim_street_spans
from proxy.policy import PiiPolicy, load_policy
from proxy.tokens import TOKEN_RE, TokenMinter
from tests.proxy.fakes import TOKEN_KEY

MINTER = TokenMinter(TOKEN_KEY, "7")


def test_tokens_are_deterministic_within_a_task() -> None:
    assert MINTER.mint("t1", "PERSON", "Ada Lovelace") == MINTER.mint("t1", "PERSON", "Ada Lovelace")


def test_tokens_differ_across_tasks_types_values_and_seeds() -> None:
    base = MINTER.mint("t1", "PERSON", "Ada Lovelace").render()
    assert MINTER.mint("t2", "PERSON", "Ada Lovelace").render() != base
    assert MINTER.mint("t1", "STREET_ADDRESS", "Ada Lovelace").render() != base
    assert MINTER.mint("t1", "PERSON", "ada lovelace").render() != base
    assert TokenMinter(TOKEN_KEY, "8").mint("t1", "PERSON", "Ada Lovelace").render() != base


def test_rendered_token_shape_and_canonical_uuid() -> None:
    token = MINTER.mint("t1", "US_SSN", "123-45-6789")
    assert TOKEN_RE.fullmatch(token.render())
    assert token.canonical.version == 8
    assert token.render().startswith("[[US_SSN:")


def test_short_keys_are_rejected() -> None:
    with pytest.raises(ValueError, match="16 bytes"):
        TokenMinter(b"short", "7")


def test_policy_loads_and_maps_structured_keys() -> None:
    policy = load_policy()
    assert policy.field_type("member_number") == "MEMBER_ID"
    assert policy.field_type("memberNumber") == "MEMBER_ID"
    assert policy.field_type("dob") == "DATE_OF_BIRTH"
    assert policy.field_type("product") is None


def test_policy_rehydration_rules() -> None:
    policy = load_policy()
    assert policy.may_rehydrate("updateAddress", "street", "STREET_ADDRESS")
    assert not policy.may_rehydrate("addNote", "body", "US_SSN")
    assert not policy.may_rehydrate("unknownTool", "x", "PERSON")


def test_policy_rejects_undeclared_and_non_rehydratable_types() -> None:
    base = load_policy().model_dump()
    with pytest.raises(ValidationError, match="undeclared"):
        PiiPolicy.model_validate({**base, "rehydration": {"t": {"a": ["NOPE"]}}})
    with pytest.raises(ValidationError, match="non-rehydratable"):
        PiiPolicy.model_validate({**base, "rehydration": {"t": {"a": ["CREDENTIAL"]}}})


def _span(start: int, end: int, entity_type: str, score: float) -> Span:
    return Span(start=start, end=end, entity_type=entity_type, score=score, source="t")


def test_a_contained_span_yields_to_the_longer_span() -> None:
    street, person = _span(0, 30, "STREET_ADDRESS", 0.7), _span(4, 13, "PERSON", 0.9)
    assert resolve_overlaps([street, person]) == [street]


def test_same_type_overlaps_are_unioned_and_partial_overlaps_keep_the_higher_score() -> None:
    merged = resolve_overlaps([_span(0, 10, "PERSON", 0.6), _span(5, 15, "PERSON", 0.8)])
    assert [(s.start, s.end, s.score) for s in merged] == [(0, 15, 0.8)]
    winner = resolve_overlaps([_span(0, 10, "PERSON", 0.6), _span(5, 15, "STREET_ADDRESS", 0.8)])
    assert [s.entity_type for s in winner] == ["STREET_ADDRESS"]


def test_street_spans_stop_before_city_state_and_zip() -> None:
    text = "Move to 420 Jerry Alley Apt. 033, Danielleville, WA 25854 please"
    start = text.index("420")
    wide = _span(start, text.index(" please"), "STREET_ADDRESS", 0.9)
    (trimmed,) = trim_street_spans([wide], text)
    assert text[trimmed.start : trimmed.end] == "420 Jerry Alley Apt. 033"
    not_a_street = _span(0, len("Springfield, IL"), "STREET_ADDRESS", 0.9)
    assert trim_street_spans([not_a_street], "Springfield, IL") == [not_a_street]


def test_city_state_zip_alone_is_not_a_street() -> None:
    text = "Danielleville, WA 25854"
    assert trim_street_spans([_span(0, len(text), "STREET_ADDRESS", 0.9)], text) == []


def test_numeric_entity_types_need_digits() -> None:
    from proxy.detection import plausible

    text = "Date of birth 03/02/1987"
    assert not plausible(_span(0, 13, "DATE_OF_BIRTH", 0.9), text)
    assert plausible(_span(14, 24, "DATE_OF_BIRTH", 0.9), text)
    assert plausible(_span(0, 4, "PERSON", 0.9), text)


def test_role_nouns_are_not_names() -> None:
    from proxy.detection import plausible

    text = "from an administrator or The user, says Ada Quinby."
    assert not plausible(_span(5, 21, "PERSON", 0.9), text)
    assert not plausible(_span(25, 33, "PERSON", 0.9), text)
    assert not plausible(_span(0, 10, "PERSON", 0.9), "Payee name")
    assert plausible(_span(40, 50, "PERSON", 0.9), text)
