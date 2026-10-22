from __future__ import annotations

from pathlib import Path

import pytest

from agent import runner
from agent.runner import TaskRun, record_index, replay_differences


def row(task_id: str, outcome: str = "success", passed: bool = True, llm_calls: int = 3) -> TaskRun:
    return TaskRun(
        task_id=task_id,
        intent="claim_status",
        scenario="healthy",
        outcome=outcome,
        passed=passed,
        wrong_action=False,
        verified=None,
        tiers=["api"],
        fallback_reason=None,
        handoff_reason=None,
        api_failures=[],
        approvals=[],
        quarantined=False,
        llm_calls=llm_calls,
        tokens_in=100,
        tokens_out=10,
        cassette_mismatches=0,
        models=["scripted"],
        seconds=1.0,
        missing=[],
        unexpected=[],
    )


@pytest.fixture(autouse=True)
def index_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "CASSETTE_INDEX", tmp_path / "index.json")


def test_a_faithful_replay_has_no_differences() -> None:
    record_index([row("claim_status-01"), row("claim_status-02", outcome="incomplete", passed=False)])
    assert replay_differences([row("claim_status-01"), row("claim_status-02", "incomplete", False)]) == []


def test_changed_outcomes_and_missing_recordings_are_reported() -> None:
    record_index([row("claim_status-01")])
    assert replay_differences([row("claim_status-01", llm_calls=4), row("claim_status-09")]) == [
        "claim_status-01: llm_calls recorded 3, replayed 4",
        "claim_status-09: no recording",
    ]


def test_recording_again_replaces_only_the_rerun_tasks() -> None:
    record_index([row("claim_status-01"), row("claim_status-02")])
    record_index([row("claim_status-01", outcome="error", passed=False)])
    assert replay_differences([row("claim_status-02")]) == []
    assert replay_differences([row("claim_status-01", outcome="error", passed=False)]) == []
