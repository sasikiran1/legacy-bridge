from __future__ import annotations

import time
from functools import cache

import pytest

from testbed import seed
from testbed.dataset import Canary, Dataset, EntityType
from testbed.tasks import Task


@cache
def generated() -> tuple[Dataset, tuple[Task, ...], tuple[Canary, ...]]:
    return seed.generate()


def test_generation_is_deterministic() -> None:
    assert seed.artifacts() == seed.artifacts()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs POSIX tzset")
def test_generation_does_not_depend_on_the_host_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    outputs = []
    for zone in ("UTC", "Asia/Kolkata", "America/Los_Angeles"):
        monkeypatch.setenv("TZ", zone)
        time.tzset()
        outputs.append(seed.artifacts())
    monkeypatch.undo()
    time.tzset()
    assert outputs[0] == outputs[1] == outputs[2]


def test_committed_artifacts_match_a_fresh_generation() -> None:
    assert seed.main(["check"]) == 0


def test_dataset_shape() -> None:
    ds, _, _ = generated()
    assert len(ds.members) == seed.MEMBER_COUNT
    assert len({m.member_number for m in ds.members}) == seed.MEMBER_COUNT
    assert len({p.policy_number for p in ds.policies}) == len(ds.policies)
    assert len({c.claim_number for c in ds.claims}) == len(ds.claims)
    assert sum(n.injected for n in ds.notes) == len(seed.load_injections())


def test_injected_notes_never_land_on_canary_members() -> None:
    ds, _, _ = generated()
    canary_ids = {m.id for m in ds.members if m.member_number in ds.canary_member_numbers}
    assert not any(n.injected and n.member_id in canary_ids for n in ds.notes)


def test_every_canary_member_contributes_each_core_entity_type() -> None:
    _, _, canaries = generated()
    core = {
        EntityType.PERSON,
        EntityType.DATE_OF_BIRTH,
        EntityType.US_SSN,
        EntityType.EMAIL_ADDRESS,
        EntityType.PHONE_NUMBER,
        EntityType.STREET_ADDRESS,
        EntityType.MEMBER_ID,
    }
    by_member: dict[str, set[EntityType]] = {}
    for canary in canaries:
        by_member.setdefault(canary.member_number, set()).add(canary.entity_type)
    assert len(by_member) == seed.CANARY_MEMBER_COUNT
    assert all(core <= types for types in by_member.values())


def test_canary_values_do_not_occur_in_other_members_data() -> None:
    ds, _, canaries = generated()
    for canary in canaries:
        if canary.entity_type in {EntityType.POLICY_NUMBER, EntityType.CLAIM_NUMBER}:
            continue
        others = [m for m in ds.members if m.member_number != canary.member_number]
        assert not any(canary.value in text for m in others for text in seed._member_texts(m)), canary


def test_sql_literals_escape_quotes() -> None:
    assert seed._sql_literal("O'Brien") == "'O''Brien'"
    assert seed._sql_literal(None) == "NULL"
    assert seed._sql_literal(True) == "TRUE"
