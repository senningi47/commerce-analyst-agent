"""Exhaustive status-machine transition matrix for the evaluation store.

Every illegal transition must fail closed with `EvalStateConflict` — the
task-level checkpoint is only trustworthy if no code path can overwrite a
terminal or stale state (v0.3 §8.5.3, §16.1).
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from commerce_agent.evaluation._memory import InMemoryEvaluationStore
from commerce_agent.evaluation.contracts import (
    AttemptRecord,
    AttemptTelemetry,
    EpisodeResult,
    EvalStateConflict,
    EvalTaskStatus,
)

FIXED_TIME = datetime(2026, 9, 12, 8, 0, tzinfo=UTC)


def make_store_with(
    status: EvalTaskStatus,
) -> tuple[InMemoryEvaluationStore, AttemptRecord]:
    store = InMemoryEvaluationStore(clock=lambda: FIXED_TIME)
    record = AttemptRecord(
        attempt_id=uuid4(),
        run_id=uuid4(),
        experiment_id="pilot-day5",
        task_id="task-1",
        mode="c",
        attempt_seq=1,
        status=EvalTaskStatus.PENDING,
        started_at=FIXED_TIME,
    )
    store.register_attempt(record)
    if status != EvalTaskStatus.PENDING:
        store.mark_running(record.attempt_id, expected_status=EvalTaskStatus.PENDING)
    if status not in {EvalTaskStatus.PENDING, EvalTaskStatus.RUNNING}:
        store.finish_attempt(
            record.attempt_id,
            status=status,
            error_class=None if status == EvalTaskStatus.SUCCEEDED else "x",
            telemetry=AttemptTelemetry(),
            result=EpisodeResult(reward=Decimal("1.0"))
            if status == EvalTaskStatus.SUCCEEDED
            else None,
        )
    return store, record


ALL_STATUSES = list(EvalTaskStatus)


@pytest.mark.parametrize("from_status", ALL_STATUSES)
@pytest.mark.parametrize("target_status", ALL_STATUSES)
def test_finish_only_from_running_with_valid_targets(
    from_status: EvalTaskStatus, target_status: EvalTaskStatus
) -> None:
    store, record = make_store_with(from_status)

    if from_status == EvalTaskStatus.RUNNING:
        if target_status == EvalTaskStatus.SUCCEEDED:
            store.finish_attempt(
                record.attempt_id,
                status=target_status,
                error_class=None,
                telemetry=AttemptTelemetry(),
                result=EpisodeResult(reward=Decimal("1.0")),
            )
        else:
            store.finish_attempt(
                record.attempt_id,
                status=target_status,
                error_class=None,
                telemetry=AttemptTelemetry(),
            )
        return  # the one legal path

    with pytest.raises(EvalStateConflict) as excinfo:
        store.finish_attempt(
            record.attempt_id,
            status=target_status,
            error_class=None,
            telemetry=AttemptTelemetry(),
        )
    assert excinfo.value.reason_code == "status_transition_conflict"


@pytest.mark.parametrize("from_status", ALL_STATUSES)
@pytest.mark.parametrize("expected", ALL_STATUSES)
def test_mark_running_guard_matrix(
    from_status: EvalTaskStatus, expected: EvalTaskStatus
) -> None:
    store, record = make_store_with(from_status)

    if expected == from_status and from_status == EvalTaskStatus.PENDING:
        store.mark_running(record.attempt_id, expected_status=expected)
        return  # the one legal path

    with pytest.raises(EvalStateConflict) as excinfo:
        store.mark_running(record.attempt_id, expected_status=expected)
    assert excinfo.value.reason_code == "status_transition_conflict"


def test_double_terminal_transitions_fail() -> None:
    store, record = make_store_with(EvalTaskStatus.SUCCEEDED)
    with pytest.raises(EvalStateConflict):
        store.finish_attempt(
            record.attempt_id,
            status=EvalTaskStatus.SUCCEEDED,
            error_class=None,
            telemetry=AttemptTelemetry(),
        )
    with pytest.raises(EvalStateConflict):
        store.finish_attempt(
            record.attempt_id,
            status=EvalTaskStatus.FAILED,
            error_class="x",
            telemetry=AttemptTelemetry(),
        )
    with pytest.raises(EvalStateConflict):
        store.record_result(record.attempt_id, EpisodeResult())


def test_result_rules() -> None:
    store, running = make_store_with(EvalTaskStatus.RUNNING)
    with pytest.raises(EvalStateConflict) as excinfo:
        store.finish_attempt(
            running.attempt_id,
            status=EvalTaskStatus.FAILED,
            error_class="x",
            telemetry=AttemptTelemetry(),
            result=EpisodeResult(reward=Decimal("1.0")),
        )
    assert excinfo.value.reason_code == "result_requires_succeeded_attempt"

    _unused, record = make_store_with(EvalTaskStatus.SUCCEEDED)
    with pytest.raises(EvalStateConflict) as excinfo:
        store.finish_attempt(
            record.attempt_id,
            status=EvalTaskStatus.FAILED,
            error_class="x",
            telemetry=AttemptTelemetry(),
        )
    assert excinfo.value.reason_code == "status_transition_conflict"


def test_unknown_attempt_transitions_fail() -> None:
    store = InMemoryEvaluationStore(clock=lambda: FIXED_TIME)
    unknown = uuid4()
    with pytest.raises(EvalStateConflict):
        store.mark_running(unknown, expected_status=EvalTaskStatus.PENDING)
    with pytest.raises(EvalStateConflict):
        store.finish_attempt(
            unknown,
            status=EvalTaskStatus.SUCCEEDED,
            error_class=None,
            telemetry=AttemptTelemetry(),
        )
    with pytest.raises(EvalStateConflict):
        store.record_result(unknown, EpisodeResult())
