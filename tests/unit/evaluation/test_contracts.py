from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from commerce_agent.evaluation.contracts import (
    RETRYABLE_STATUSES,
    TERMINAL_STATUSES,
    AttemptRecord,
    AttemptTelemetry,
    EpisodeResult,
    EvalStateConflict,
    EvalTaskStatus,
)

NOW = datetime(2026, 9, 12, 3, 0, tzinfo=UTC)


def attempt_record(**overrides: object) -> AttemptRecord:
    values: dict[str, object] = {
        "attempt_id": uuid4(),
        "run_id": uuid4(),
        "experiment_id": "pilot-day5",
        "task_id": "task-1",
        "mode": "c",
        "attempt_seq": 1,
        "status": EvalTaskStatus.PENDING,
        "started_at": NOW,
    }
    values.update(overrides)
    return AttemptRecord(**values)  # type: ignore[arg-type]


def test_status_enum_matches_frozen_official_states() -> None:
    assert {status.value for status in EvalTaskStatus} == {
        "pending",
        "running",
        "succeeded",
        "failed",
        "infrastructure_error",
        "interrupted",
    }
    assert TERMINAL_STATUSES == {
        EvalTaskStatus.SUCCEEDED,
        EvalTaskStatus.FAILED,
    }
    assert RETRYABLE_STATUSES == {
        EvalTaskStatus.PENDING,
        EvalTaskStatus.RUNNING,
        EvalTaskStatus.INFRASTRUCTURE_ERROR,
        EvalTaskStatus.INTERRUPTED,
    }


def test_attempt_record_defaults_and_bounds() -> None:
    record = attempt_record()
    assert record.status == EvalTaskStatus.PENDING
    assert record.telemetry == AttemptTelemetry()
    assert record.finished_at is None
    with pytest.raises(ValidationError):
        attempt_record(attempt_seq=0)
    with pytest.raises(ValidationError):
        attempt_record(mode="b")


def test_telemetry_bounds_fail_closed() -> None:
    with pytest.raises(ValidationError):
        AttemptTelemetry(cache_hit_ratio=1.5)
    with pytest.raises(ValidationError):
        AttemptTelemetry(wall_clock_ms=-1)
    with pytest.raises(ValidationError):
        AttemptTelemetry(spool_sha256="not-a-digest")
    telemetry = AttemptTelemetry(cache_hit_ratio=0.5, rounds=3)
    assert telemetry.cache_hit_ratio == 0.5


def test_telemetry_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AttemptTelemetry.model_validate({"sol_sql": "leak"})


def test_episode_result_defaults_are_none_and_reward_bounded() -> None:
    result = EpisodeResult()
    assert result.reward is None and result.phase1_passed is None
    assert EpisodeResult(reward=Decimal("0.75")).reward == Decimal("0.75")
    with pytest.raises(ValidationError):
        EpisodeResult(reward=Decimal(-1))


def test_eval_state_conflict_carries_reason_code() -> None:
    error = EvalStateConflict("status_transition_conflict")
    assert error.reason_code == "status_transition_conflict"
    assert str(error) == "status_transition_conflict"
