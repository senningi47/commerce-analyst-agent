import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from commerce_agent.evaluation.contracts import (
    AttemptRecord,
    EpisodeResult,
    EvalTaskStatus,
)
from commerce_agent.evaluation.episode import EpisodeOutcome, EpisodeTask, StubEpisodeExecutor

FIXED_TIME = datetime(2026, 9, 12, 5, 0, tzinfo=UTC)


def make_attempt() -> AttemptRecord:
    return AttemptRecord(
        attempt_id=uuid4(),
        run_id=uuid4(),
        experiment_id="pilot-day5",
        task_id="task-1",
        mode="c",
        attempt_seq=1,
        status=EvalTaskStatus.RUNNING,
        started_at=FIXED_TIME,
    )


def make_outcome(status: EvalTaskStatus = EvalTaskStatus.SUCCEEDED) -> EpisodeOutcome:
    return EpisodeOutcome(
        attempt_id=uuid4(),
        status=status,
        result=EpisodeResult(reward=1.0) if status == EvalTaskStatus.SUCCEEDED else None,
    )


def test_episode_task_bounds() -> None:
    task = EpisodeTask(task_id="dbx-1", mode="a")
    assert task.task_id == "dbx-1"
    with pytest.raises(Exception, match="mode"):
        EpisodeTask(task_id="dbx-1", mode="b")  # type: ignore[arg-type]


def test_episode_outcome_defaults() -> None:
    outcome = EpisodeOutcome(attempt_id=uuid4(), status=EvalTaskStatus.FAILED)
    assert outcome.error_class is None
    assert outcome.result is None
    assert outcome.telemetry.rounds is None


def test_stub_executor_scripts_and_records() -> None:
    attempt = make_attempt()
    task = EpisodeTask(task_id="task-1", mode="c")
    outcomes = [make_outcome(), make_outcome(EvalTaskStatus.FAILED)]
    executor = StubEpisodeExecutor(outcomes)

    first = asyncio.run(executor.execute(task, attempt))
    second = asyncio.run(executor.execute(task, attempt))

    assert first.status == EvalTaskStatus.SUCCEEDED
    assert second.status == EvalTaskStatus.FAILED
    assert executor.executed == [(task, attempt), (task, attempt)]


def test_stub_executor_fallback_when_script_exhausted() -> None:
    attempt = make_attempt()
    executor = StubEpisodeExecutor(
        [],
        fallback=lambda task, record: EpisodeOutcome(
            attempt_id=record.attempt_id,
            status=EvalTaskStatus.INFRASTRUCTURE_ERROR,
            error_class="ambiguous_delivery",
        ),
    )

    outcome = asyncio.run(executor.execute(EpisodeTask(task_id="t", mode="a"), attempt))

    assert outcome.status == EvalTaskStatus.INFRASTRUCTURE_ERROR
    assert outcome.error_class == "ambiguous_delivery"
    assert outcome.attempt_id == attempt.attempt_id
