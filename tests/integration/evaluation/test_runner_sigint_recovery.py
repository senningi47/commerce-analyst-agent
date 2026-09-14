"""SIGINT recovery drill: real PostgreSQL, scripted executor, zero paid calls.

Day 6 acceptance gate 2 (v0.3 §19.3 last item): after a human interrupt the
runner must not repeat completed tasks and an interrupted task must produce a
fresh attempt from the task start. Run 1 drives the exact effect of
``cli._bridge_signals`` - the stop_event fires while one episode is
mid-flight, one task has never been claimed - against the live eval schema;
run 2 restarts the same experiment and asserts the recovery shape row by row.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest

from commerce_agent.evaluation._postgres import PostgresEvaluationStore
from commerce_agent.evaluation.contracts import EpisodeResult, EvalTaskStatus
from commerce_agent.evaluation.episode import EpisodeOutcome, EpisodeTask
from commerce_agent.evaluation.runner import (
    EvaluationEventLog,
    EvaluationRunner,
    RunnerConfig,
)

pytestmark = pytest.mark.postgres

FIXED_TIME = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)

TASK_LIST = (
    EpisodeTask(task_id="sigint-a1", mode="c"),
    EpisodeTask(task_id="sigint-b2", mode="c"),
    EpisodeTask(task_id="sigint-hang", mode="a"),
    EpisodeTask(task_id="sigint-p4", mode="c"),
)


class DrillExecutor:
    """t1/t2 succeed; the hang task signals stop, then waits for cancellation."""

    def __init__(self, stop_event: asyncio.Event, *, hang_task: str | None) -> None:
        self._stop_event = stop_event
        self._hang_task = hang_task
        self.executed: list[tuple[str, str, int]] = []

    async def execute(self, task: EpisodeTask, attempt) -> EpisodeOutcome:  # type: ignore[no-untyped-def]
        self.executed.append((task.task_id, task.mode, attempt.attempt_seq))
        if task.task_id == self._hang_task:
            # deterministic equivalent of SIGINT arriving mid-episode
            self._stop_event.set()
            await asyncio.Event().wait()
        return EpisodeOutcome(
            attempt_id=attempt.attempt_id,
            status=EvalTaskStatus.SUCCEEDED,
            result=EpisodeResult(reward=1.0, phase1_passed=True),
        )


def make_runner(
    experiment_id: str,
    executor: DrillExecutor,
    stop_event: asyncio.Event,
    tmp_path,
    run_key: str,
) -> EvaluationRunner:
    config = RunnerConfig(
        experiment_id=experiment_id,
        purpose="pilot",
        config_hash="b" * 64,
        task_list=TASK_LIST,
        concurrency=1,
        stop_grace_seconds=1,
        task_order_seed=0,
    )
    return EvaluationRunner(
        store=PostgresEvaluationStore(dsn=os.environ["PRODUCT_EVALUATION_DATABASE_DSN"]),
        executor=executor,
        events=EvaluationEventLog(tmp_path / f"events-{run_key}.jsonl"),
        config=config,
        stop_event=stop_event,
        run_id=uuid4(),
        clock=lambda: FIXED_TIME,
    )


def experiment_rows(experiment_id: str) -> list[tuple[str, str, int, str]]:
    admin_dsn = os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        cursor = connection.execute(
            "SELECT task_id, mode, attempt_seq, status FROM eval.task_attempt "
            "WHERE experiment_id = %s ORDER BY task_id, mode, attempt_seq",
            (experiment_id,),
        )
        return [
            (task_id, mode, attempt_seq, status)
            for task_id, mode, attempt_seq, status in cursor.fetchall()
        ]


@pytest.fixture
def scoped_drill() -> str:
    experiment_id = f"sigint-drill-{uuid.uuid4().hex[:12]}"
    admin_dsn = os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    try:
        yield experiment_id
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "DELETE FROM eval.task_result WHERE attempt_id IN "
                "(SELECT attempt_id FROM eval.task_attempt WHERE experiment_id = %s)",
                (experiment_id,),
            )
            connection.execute(
                "DELETE FROM eval.task_attempt WHERE experiment_id = %s",
                (experiment_id,),
            )
            connection.execute(
                "DELETE FROM eval.experiment WHERE experiment_id = %s",
                (experiment_id,),
            )


def test_sigint_midrun_then_recovery_on_same_experiment(scoped_drill: str, tmp_path) -> None:
    # ── run 1: two completions, one mid-flight hang, one never claimed ──
    stop_event = asyncio.Event()
    executor = DrillExecutor(stop_event, hang_task="sigint-hang")
    runner = make_runner(scoped_drill, executor, stop_event, tmp_path, "run1")

    summary = asyncio.run(runner.run())

    assert summary.stopped is True
    assert summary.attempted_episodes == 3
    assert summary.completed_tasks == 2
    assert summary.unfinished_tasks == 1
    assert summary.status_counts == {"succeeded": 2, "interrupted": 1}
    assert executor.executed == [
        ("sigint-a1", "c", 1),
        ("sigint-b2", "c", 1),
        ("sigint-hang", "a", 1),
    ]

    rows = experiment_rows(scoped_drill)
    assert rows == [
        ("sigint-a1", "c", 1, "succeeded"),
        ("sigint-b2", "c", 1, "succeeded"),
        ("sigint-hang", "a", 1, "interrupted"),
    ]  # the pending task has no attempt row at all

    # ── run 2: same experiment, no interrupt; interrupted task gets seq 2 ──
    second_stop = asyncio.Event()
    second_executor = DrillExecutor(second_stop, hang_task=None)
    second = make_runner(scoped_drill, second_executor, second_stop, tmp_path, "run2")

    second_summary = asyncio.run(second.run())

    assert second_summary.stopped is False
    assert second_summary.attempted_episodes == 2
    assert second_summary.completed_tasks == 4
    assert second_summary.unfinished_tasks == 0
    assert second_summary.status_counts == {"succeeded": 2}
    assert sorted(second_executor.executed) == [
        ("sigint-hang", "a", 2),
        ("sigint-p4", "c", 1),
    ]

    rows = experiment_rows(scoped_drill)
    assert rows == [
        ("sigint-a1", "c", 1, "succeeded"),
        ("sigint-b2", "c", 1, "succeeded"),
        ("sigint-hang", "a", 1, "interrupted"),
        ("sigint-hang", "a", 2, "succeeded"),
        ("sigint-p4", "c", 1, "succeeded"),
    ]

    # ── event log crosses with the eval table state machine ──
    log = EvaluationEventLog(tmp_path / "events-run1.jsonl").read_all()
    log += EvaluationEventLog(tmp_path / "events-run2.jsonl").read_all()
    assert [event["event"] for event in log] == [
        "attempt_started",  # a1
        "attempt_finished",
        "attempt_started",  # b2
        "attempt_finished",
        "attempt_started",  # hang seq 1
        "attempt_interrupted",
        "attempt_started",  # hang seq 2 (new run)
        "attempt_finished",
        "attempt_started",  # p4 (new run)
        "attempt_finished",
    ]
    interrupted = log[5]
    assert "status" not in interrupted  # the event type itself is the signal
    assert (interrupted["task_id"], interrupted["attempt_seq"]) == ("sigint-hang", 1)
    resumed = log[6]
    assert (resumed["task_id"], resumed["attempt_seq"]) == ("sigint-hang", 2)
    assert resumed["run_id"] != log[0]["run_id"]  # a fresh run id per restart
    for event in log:
        assert event["experiment_id"] == scoped_drill
