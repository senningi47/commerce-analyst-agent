"""Stub end-to-end run of the EvaluationRunner: resume, ordering, events.

Zero network, zero database, zero paid calls — the executor is scripted and
the store is in-memory. This is the deterministic evidence that the runner's
task-level recovery behaves as v0.3 §8.5.3 requires.
"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from commerce_agent.evaluation._memory import InMemoryEvaluationStore
from commerce_agent.evaluation.contracts import (
    AttemptRecord,
    AttemptTelemetry,
    EpisodeResult,
    EvalTaskStatus,
)
from commerce_agent.evaluation.episode import EpisodeOutcome, EpisodeTask, StubEpisodeExecutor
from commerce_agent.evaluation.runner import EvaluationEventLog, EvaluationRunner, RunnerConfig

FIXED_TIME = datetime(2026, 9, 12, 7, 0, tzinfo=UTC)


def make_attempt_record(task: EpisodeTask, seq: int, status: EvalTaskStatus) -> AttemptRecord:
    return AttemptRecord(
        attempt_id=uuid4(),
        run_id=uuid4(),
        experiment_id="pilot-day5",
        task_id=task.task_id,
        mode=task.mode,
        attempt_seq=seq,
        status=status,
        started_at=FIXED_TIME,
    )


def seed_interrupted(store: InMemoryEvaluationStore, task: EpisodeTask, seq: int) -> None:
    record = make_attempt_record(task, seq, EvalTaskStatus.PENDING)
    store.register_attempt(record)
    store.mark_running(record.attempt_id, expected_status=EvalTaskStatus.PENDING)
    store.finish_attempt(
        record.attempt_id,
        status=EvalTaskStatus.INTERRUPTED,
        error_class=None,
        telemetry=AttemptTelemetry(),
    )


def test_stub_end_to_end_run_and_resume(tmp_path) -> None:
    store = InMemoryEvaluationStore(clock=lambda: FIXED_TIME)
    task_list = (
        EpisodeTask(task_id="task-1", mode="c"),
        EpisodeTask(task_id="task-2", mode="c"),
        EpisodeTask(task_id="task-3", mode="a"),
        EpisodeTask(task_id="task-4", mode="a"),
    )
    # seed a previously interrupted attempt for task-3 (crash residue)
    seed_interrupted(store, task_list[2], 1)

    config = RunnerConfig(
        experiment_id="pilot-day5",
        purpose="pilot",
        config_hash="a" * 64,
        task_list=task_list,
        concurrency=2,
        stop_grace_seconds=1,
        task_order_seed=0,
    )
    executor = StubEpisodeExecutor(
        [
            EpisodeOutcome(attempt_id=uuid4(), status=EvalTaskStatus.SUCCEEDED,
                           result=EpisodeResult(reward=1.0, phase1_passed=True)),
            EpisodeOutcome(attempt_id=uuid4(), status=EvalTaskStatus.FAILED,
                           error_class="evaluator_rejected"),
            EpisodeOutcome(attempt_id=uuid4(), status=EvalTaskStatus.SUCCEEDED,
                           result=EpisodeResult(reward=0.25, phase1_passed=True,
                                                phase2_passed=True)),
            EpisodeOutcome(attempt_id=uuid4(), status=EvalTaskStatus.SUCCEEDED,
                           result=EpisodeResult(reward=0.0, phase1_passed=False)),
        ],
        fallback=lambda task, attempt: EpisodeOutcome(
            attempt_id=attempt.attempt_id, status=EvalTaskStatus.FAILED,
            error_class="unexpected_default",
        ),
    )
    events = EvaluationEventLog(tmp_path / "events.jsonl")
    runner = EvaluationRunner(
        store=store, executor=executor, events=events, config=config,
        clock=lambda: FIXED_TIME,
    )

    summary = asyncio.run(runner.run())

    assert summary.attempted_episodes == 4
    assert summary.completed_tasks == 4
    assert summary.unfinished_tasks == 0
    assert summary.status_counts == {"succeeded": 3, "failed": 1}
    assert summary.stopped is False

    # attempt_seq: task-3 resumed at seq 2; others started at 1
    seqs = sorted(
        (attempt.task_id, attempt.mode, attempt.attempt_seq)
        for _task, attempt in executor.executed
    )
    assert ("task-3", "a", 2) in seqs
    assert all(
        (task_id, mode, 1) not in seqs
        for task_id, mode in [("task-3", "a")]
    )

    # event log is valid JSONL with started/finished pairs in order
    log = events.read_all()
    assert len(log) == 8
    for index in range(0, 8, 2):
        assert log[index]["event"] == "attempt_started"
        assert log[index + 1]["event"] == "attempt_finished"
        assert log[index]["attempt_id"] == log[index + 1]["attempt_id"]

    # rerunning with the same store executes nothing new
    executor_second = StubEpisodeExecutor([], fallback=lambda task, attempt: EpisodeOutcome(
        attempt_id=attempt.attempt_id, status=EvalTaskStatus.FAILED,
        error_class="should_not_run",
    ))
    second = EvaluationRunner(
        store=store, executor=executor_second, events=events, config=config,
        clock=lambda: FIXED_TIME,
    )
    second_summary = asyncio.run(second.run())
    assert second_summary.attempted_episodes == 0
    assert second_summary.completed_tasks == 4
    assert second_summary.status_counts == {}
    assert executor_second.executed == []
