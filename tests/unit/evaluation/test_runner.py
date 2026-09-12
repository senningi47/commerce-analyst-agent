import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from commerce_agent.evaluation._memory import InMemoryEvaluationStore
from commerce_agent.evaluation.contracts import (
    AttemptTelemetry,
    EpisodeResult,
    EvalTaskStatus,
)
from commerce_agent.evaluation.episode import EpisodeOutcome, EpisodeTask, StubEpisodeExecutor
from commerce_agent.evaluation.runner import EvaluationEventLog, EvaluationRunner, RunnerConfig

FIXED_TIME = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
CONFIG_HASH = "a" * 64


def make_config(**overrides: object) -> RunnerConfig:
    values: dict[str, object] = {
        "experiment_id": "pilot-day5",
        "purpose": "pilot",
        "config_hash": CONFIG_HASH,
        "task_list": (
            EpisodeTask(task_id="task-1", mode="c"),
            EpisodeTask(task_id="task-2", mode="a"),
            EpisodeTask(task_id="task-3", mode="c"),
        ),
        "concurrency": 2,
        "stop_grace_seconds": 1,
        "task_order_seed": 0,
    }
    values.update(overrides)
    return RunnerConfig(**values)  # type: ignore[arg-type]


def make_outcome(status: EvalTaskStatus, reward: float | None = None) -> EpisodeOutcome:
    return EpisodeOutcome(
        attempt_id=uuid4(),
        status=status,
        result=EpisodeResult(reward=reward) if reward is not None else None,
    )


class BlockingExecutor(StubEpisodeExecutor):
    """Waits on an event before returning, to hold episodes in flight."""

    def __init__(self, release: asyncio.Event) -> None:
        super().__init__([])
        self._release = release
        self.in_flight = 0
        self.max_in_flight = 0

    async def execute(self, task: EpisodeTask, attempt: object) -> EpisodeOutcome:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await self._release.wait()
        finally:
            self.in_flight -= 1
        return EpisodeOutcome(attempt_id=attempt.attempt_id, status=EvalTaskStatus.SUCCEEDED)  # type: ignore[union-attr]


def make_runner(
    store: InMemoryEvaluationStore,
    executor: object,
    tmp_path: object,
    config: RunnerConfig | None = None,
    stop_event: asyncio.Event | None = None,
) -> EvaluationRunner:
    return EvaluationRunner(
        store=store,
        executor=executor,  # type: ignore[arg-type]
        events=EvaluationEventLog(tmp_path / "events.jsonl"),
        config=config or make_config(),
        stop_event=stop_event,
        clock=lambda: FIXED_TIME,
    )


def test_run_executes_all_pending_tasks_and_records_results(tmp_path) -> None:
    store = InMemoryEvaluationStore(clock=lambda: FIXED_TIME)
    executor = StubEpisodeExecutor(
        [
            make_outcome(EvalTaskStatus.SUCCEEDED, reward=1.0),
            make_outcome(EvalTaskStatus.FAILED),
            make_outcome(EvalTaskStatus.SUCCEEDED, reward=0.5),
        ]
    )
    runner = make_runner(store, executor, tmp_path)

    summary = asyncio.run(runner.run())

    assert summary.attempted_episodes == 3
    assert summary.completed_tasks == 3
    assert summary.unfinished_tasks == 0
    assert summary.status_counts == {"succeeded": 2, "failed": 1}
    assert summary.stopped is False
    assert store.attempt_count() == 3
    assert len(executor.executed) == 3
    events = EvaluationEventLog(tmp_path / "events.jsonl").read_all()
    assert len(events) == 6  # started + finished per episode
    assert {event["event"] for event in events} == {"attempt_started", "attempt_finished"}


def test_completed_tasks_never_rerun(tmp_path) -> None:
    store = InMemoryEvaluationStore(clock=lambda: FIXED_TIME)
    done = EpisodeTask(task_id="task-1", mode="c")
    from commerce_agent.evaluation.contracts import AttemptRecord

    finished = AttemptRecord(
        attempt_id=uuid4(),
        run_id=uuid4(),
        experiment_id="pilot-day5",
        task_id=done.task_id,
        mode=done.mode,
        attempt_seq=1,
        status=EvalTaskStatus.PENDING,
        started_at=FIXED_TIME,
    )
    store.register_attempt(finished)
    store.mark_running(finished.attempt_id, expected_status=EvalTaskStatus.PENDING)
    store.finish_attempt(
        finished.attempt_id,
        status=EvalTaskStatus.SUCCEEDED,
        error_class=None,
        telemetry=AttemptTelemetry(),
        result=EpisodeResult(reward=Decimal("1.0")),
    )

    executor = StubEpisodeExecutor(
        [make_outcome(EvalTaskStatus.SUCCEEDED), make_outcome(EvalTaskStatus.SUCCEEDED)],
        fallback=lambda task, attempt: make_outcome(EvalTaskStatus.SUCCEEDED),
    )
    runner = make_runner(store, executor, tmp_path)
    asyncio.run(runner.run())

    executed_tasks = [(task.task_id, task.mode) for task, _attempt in executor.executed]
    assert (done.task_id, done.mode) not in executed_tasks
    assert len(executed_tasks) == 2


def test_interrupted_task_gets_new_attempt_on_resume(tmp_path) -> None:
    store = InMemoryEvaluationStore(clock=lambda: FIXED_TIME)
    first = EpisodeTask(task_id="task-1", mode="c")
    from commerce_agent.evaluation.contracts import AttemptRecord

    interrupted = AttemptRecord(
        attempt_id=uuid4(),
        run_id=uuid4(),
        experiment_id="pilot-day5",
        task_id=first.task_id,
        mode=first.mode,
        attempt_seq=1,
        status=EvalTaskStatus.PENDING,
        started_at=FIXED_TIME,
    )
    store.register_attempt(interrupted)
    store.mark_running(interrupted.attempt_id, expected_status=EvalTaskStatus.PENDING)
    store.finish_attempt(
        interrupted.attempt_id,
        status=EvalTaskStatus.INTERRUPTED,
        error_class=None,
        telemetry=AttemptTelemetry(),
    )

    executor = StubEpisodeExecutor(
        [make_outcome(EvalTaskStatus.SUCCEEDED, reward=1.0)],
        fallback=lambda task, attempt: make_outcome(EvalTaskStatus.SUCCEEDED),
    )
    runner = make_runner(store, executor, tmp_path)
    summary = asyncio.run(runner.run())

    resumed = [
        attempt
        for _task, attempt in executor.executed
        if attempt.task_id == first.task_id and attempt.mode == first.mode
    ]
    assert len(resumed) == 1
    assert resumed[0].attempt_seq == 2
    assert summary.completed_tasks == 3


def test_stop_event_stops_claiming_and_marks_in_flight_interrupted(tmp_path) -> None:
    store = InMemoryEvaluationStore(clock=lambda: FIXED_TIME)
    release = asyncio.Event()
    executor = BlockingExecutor(release)
    stop_event = asyncio.Event()
    config = make_config(
        task_list=(
            EpisodeTask(task_id="task-1", mode="c"),
            EpisodeTask(task_id="task-2", mode="a"),
        ),
        concurrency=2,
        stop_grace_seconds=1,
    )
    runner = make_runner(store, executor, tmp_path, config=config, stop_event=stop_event)

    async def scenario() -> object:
        async def stop_soon() -> None:
            await asyncio.sleep(0.05)
            stop_event.set()

        stopper = asyncio.create_task(stop_soon())
        summary = await runner.run()
        await stopper
        release.set()
        return summary

    summary = asyncio.run(scenario())

    assert summary.stopped is True
    unfinished = store.unfinished_attempts("pilot-day5")
    assert len(unfinished) == 2
    assert all(record.status == EvalTaskStatus.INTERRUPTED for record in unfinished)
    assert all(record.finished_at is not None for record in unfinished)


def test_concurrency_is_bounded_by_semaphore(tmp_path) -> None:
    store = InMemoryEvaluationStore(clock=lambda: FIXED_TIME)
    release = asyncio.Event()
    executor = BlockingExecutor(release)
    config = make_config(
        task_list=tuple(
            EpisodeTask(task_id=f"task-{index}", mode="c") for index in range(6)
        ),
        concurrency=2,
        stop_grace_seconds=1,
    )
    runner = make_runner(store, executor, tmp_path, config=config)

    async def scenario() -> None:
        stopper = asyncio.create_task(_release_after(release, 0.2))
        await asyncio.wait_for(runner.run(), timeout=5)
        await stopper

    asyncio.run(scenario())
    assert executor.max_in_flight <= 2


async def _release_after(event: asyncio.Event, delay: float) -> None:
    await asyncio.sleep(delay)
    event.set()


def test_task_order_seed_is_deterministic(tmp_path) -> None:
    tasks = tuple(
        EpisodeTask(task_id=f"task-{index}", mode="c") for index in range(5)
    )
    first = make_config(task_list=tasks, task_order_seed=7)
    second = make_config(task_list=tasks, task_order_seed=7)
    third = make_config(task_list=tasks, task_order_seed=8)

    store_a = InMemoryEvaluationStore(clock=lambda: FIXED_TIME)
    store_b = InMemoryEvaluationStore(clock=lambda: FIXED_TIME)
    ordered_a = EvaluationRunner(
        store=store_a,
        executor=StubEpisodeExecutor([]),
        events=EvaluationEventLog(tmp_path / "a.jsonl"),
        config=first,
        clock=lambda: FIXED_TIME,
    )._ordered_queue(exclude=frozenset())
    ordered_b = EvaluationRunner(
        store=store_b,
        executor=StubEpisodeExecutor([]),
        events=EvaluationEventLog(tmp_path / "b.jsonl"),
        config=second,
        clock=lambda: FIXED_TIME,
    )._ordered_queue(exclude=frozenset())
    ordered_c = EvaluationRunner(
        store=store_b,
        executor=StubEpisodeExecutor([]),
        events=EvaluationEventLog(tmp_path / "c.jsonl"),
        config=third,
        clock=lambda: FIXED_TIME,
    )._ordered_queue(exclude=frozenset())

    assert [(t.task_id, t.mode) for t in ordered_a] == [(t.task_id, t.mode) for t in ordered_b]
    assert [(t.task_id, t.mode) for t in ordered_a] != [(t.task_id, t.mode) for t in ordered_c]


def test_executor_exception_becomes_infrastructure_error(tmp_path) -> None:
    class ExplodingExecutor(StubEpisodeExecutor):
        async def execute(self, task: EpisodeTask, attempt: object) -> EpisodeOutcome:
            raise RuntimeError("boom")

    store = InMemoryEvaluationStore(clock=lambda: FIXED_TIME)
    runner = make_runner(store, ExplodingExecutor([]), tmp_path)
    summary = asyncio.run(runner.run())

    assert summary.status_counts == {"infrastructure_error": 3}
    unfinished = store.unfinished_attempts("pilot-day5")
    assert len(unfinished) == 3
    assert all(record.error_class == "executor_exception" for record in unfinished)


def test_register_experiment_called_once_with_frozen_identity(tmp_path) -> None:
    store = InMemoryEvaluationStore(clock=lambda: FIXED_TIME)
    executor = StubEpisodeExecutor(
        [make_outcome(EvalTaskStatus.SUCCEEDED)],
        fallback=lambda task, attempt: make_outcome(EvalTaskStatus.SUCCEEDED),
    )
    runner = make_runner(store, executor, tmp_path)
    asyncio.run(runner.run())

    assert store.experiment("pilot-day5") == ("pilot", CONFIG_HASH)
