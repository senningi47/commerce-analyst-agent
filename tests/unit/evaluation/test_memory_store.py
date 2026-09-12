import threading
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

FIXED_TIME = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)


def fixed_clock() -> datetime:
    return FIXED_TIME


def make_store() -> InMemoryEvaluationStore:
    return InMemoryEvaluationStore(clock=fixed_clock)


def make_record(
    *,
    task_id: str = "task-1",
    mode: str = "c",
    attempt_seq: int = 1,
    status: EvalTaskStatus = EvalTaskStatus.PENDING,
    experiment_id: str = "pilot-day5",
) -> AttemptRecord:
    return AttemptRecord(
        attempt_id=uuid4(),
        run_id=uuid4(),
        experiment_id=experiment_id,
        task_id=task_id,
        mode=mode,
        attempt_seq=attempt_seq,
        status=status,
        started_at=FIXED_TIME,
    )


def test_register_experiment_is_idempotent_and_conflict_guarded() -> None:
    store = make_store()
    store.register_experiment(
        experiment_id="pilot-day5", purpose="pilot", config_hash="a" * 64
    )
    store.register_experiment(
        experiment_id="pilot-day5", purpose="pilot", config_hash="a" * 64
    )
    with pytest.raises(EvalStateConflict) as excinfo:
        store.register_experiment(
            experiment_id="pilot-day5", purpose="pilot", config_hash="b" * 64
        )
    assert excinfo.value.reason_code == "experiment_identity_conflict"


def test_full_lifecycle_transitions() -> None:
    store = make_store()
    record = make_record()
    store.register_attempt(record)

    store.mark_running(record.attempt_id, expected_status=EvalTaskStatus.PENDING)
    assert store.attempt(record.attempt_id).status == EvalTaskStatus.RUNNING  # type: ignore[union-attr]

    store.finish_attempt(
        record.attempt_id,
        status=EvalTaskStatus.SUCCEEDED,
        error_class=None,
        telemetry=AttemptTelemetry(rounds=3, wall_clock_ms=1500),
    )
    finished = store.attempt(record.attempt_id)
    assert finished is not None
    assert finished.status == EvalTaskStatus.SUCCEEDED
    assert finished.finished_at == FIXED_TIME
    assert finished.telemetry.rounds == 3


def test_status_guards_fail_closed() -> None:
    store = make_store()
    record = make_record()
    store.register_attempt(record)

    with pytest.raises(EvalStateConflict):
        store.mark_running(record.attempt_id, expected_status=EvalTaskStatus.RUNNING)
    store.mark_running(record.attempt_id, expected_status=EvalTaskStatus.PENDING)
    with pytest.raises(EvalStateConflict):
        store.mark_running(record.attempt_id, expected_status=EvalTaskStatus.PENDING)

    pending = make_record(task_id="task-2")
    store.register_attempt(pending)
    with pytest.raises(EvalStateConflict):
        store.finish_attempt(
            pending.attempt_id,
            status=EvalTaskStatus.SUCCEEDED,
            error_class=None,
            telemetry=AttemptTelemetry(),
        )


def test_succeeded_attempts_cannot_carry_error_class() -> None:
    store = make_store()
    record = make_record()
    store.register_attempt(record)
    store.mark_running(record.attempt_id, expected_status=EvalTaskStatus.PENDING)

    with pytest.raises(EvalStateConflict) as excinfo:
        store.finish_attempt(
            record.attempt_id,
            status=EvalTaskStatus.SUCCEEDED,
            error_class="something_failed",
            telemetry=AttemptTelemetry(),
        )
    assert excinfo.value.reason_code == "succeeded_requires_no_error_class"


def test_duplicate_identities_fail_closed() -> None:
    store = make_store()
    record = make_record()
    store.register_attempt(record)
    with pytest.raises(EvalStateConflict) as excinfo:
        store.register_attempt(record)
    assert excinfo.value.reason_code == "attempt_identity_conflict"

    same_identity = make_record(
        task_id=record.task_id,
        mode=record.mode,
        attempt_seq=1,
        experiment_id=record.experiment_id,
    )
    # the in-memory identity check is per run_id; rebuilding with the same
    # run_id reproduces the unique (run_id, task_id, mode, attempt_seq) row
    same_identity = same_identity.model_copy(update={"run_id": record.run_id})
    with pytest.raises(EvalStateConflict) as excinfo:
        store.register_attempt(same_identity)
    assert excinfo.value.reason_code == "attempt_sequence_conflict"


def test_completed_tasks_and_unfinished_attempts() -> None:
    store = make_store()

    succeeded = make_record(task_id="task-1", mode="c")
    store.register_attempt(succeeded)
    store.mark_running(succeeded.attempt_id, expected_status=EvalTaskStatus.PENDING)
    store.finish_attempt(
        succeeded.attempt_id,
        status=EvalTaskStatus.SUCCEEDED,
        error_class=None,
        telemetry=AttemptTelemetry(),
    )

    failed = make_record(task_id="task-2", mode="a")
    store.register_attempt(failed)
    store.mark_running(failed.attempt_id, expected_status=EvalTaskStatus.PENDING)
    store.finish_attempt(
        failed.attempt_id,
        status=EvalTaskStatus.FAILED,
        error_class="evaluator_rejected",
        telemetry=AttemptTelemetry(),
    )

    interrupted_first = make_record(task_id="task-3", mode="c", attempt_seq=1)
    store.register_attempt(interrupted_first)
    store.mark_running(interrupted_first.attempt_id, expected_status=EvalTaskStatus.PENDING)
    store.finish_attempt(
        interrupted_first.attempt_id,
        status=EvalTaskStatus.INTERRUPTED,
        error_class=None,
        telemetry=AttemptTelemetry(),
    )
    retry = make_record(task_id="task-3", mode="c", attempt_seq=2)
    store.register_attempt(retry)

    assert store.completed_tasks("pilot-day5") == {("task-1", "c"), ("task-2", "a")}
    unfinished = store.unfinished_attempts("pilot-day5")
    assert [record.attempt_id for record in unfinished] == [retry.attempt_id]


def test_record_result_guard() -> None:
    store = make_store()
    record = make_record()
    store.register_attempt(record)
    result = EpisodeResult(
        reward=Decimal("0.5"), phase1_passed=True, phase2_passed=False, rounds=2
    )

    with pytest.raises(EvalStateConflict):
        store.record_result(record.attempt_id, result)

    store.mark_running(record.attempt_id, expected_status=EvalTaskStatus.PENDING)
    store.finish_attempt(
        record.attempt_id,
        status=EvalTaskStatus.SUCCEEDED,
        error_class=None,
        telemetry=AttemptTelemetry(),
    )
    store.record_result(record.attempt_id, result)
    assert store.result(record.attempt_id) == result
    with pytest.raises(EvalStateConflict) as excinfo:
        store.record_result(record.attempt_id, result)
    assert excinfo.value.reason_code == "result_already_recorded"


def test_store_is_thread_safe_under_concurrent_transitions() -> None:
    store = make_store()
    records = [make_record(task_id=f"task-{index}") for index in range(8)]
    for record in records:
        store.register_attempt(record)

    def start_all() -> None:
        for record in records:
            try:
                store.mark_running(record.attempt_id, expected_status=EvalTaskStatus.PENDING)
            except EvalStateConflict:
                pass  # the losing concurrent transition is the expected outcome

    threads = [threading.Thread(target=start_all) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = sum(
        1
        for record in records
        if store.attempt(record.attempt_id) is not None
        and store.attempt(record.attempt_id).status == EvalTaskStatus.RUNNING  # type: ignore[union-attr]
    )
    assert winners == 8


def test_other_experiment_scopes_are_isolated() -> None:
    store = make_store()
    record = make_record(experiment_id="other-experiment")
    store.register_attempt(record)

    assert store.completed_tasks("pilot-day5") == frozenset()
    assert store.unfinished_attempts("pilot-day5") == ()
