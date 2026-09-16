"""EvaluationRunner: bounded-concurrency, task-level recoverable evaluation loop.

Recovery semantics (v0.3 §8.5.3, §16.1): only completed tasks and unfinished
attempt metadata survive; a completed task never re-runs; an interrupted or
infrastructure-failed episode is discarded and retried from the task start
under a fresh attempt. Each task's terminal transition and public result are
written in one atomic store operation. `stop_event` stops claiming new tasks;
in-flight episodes get a bounded grace period before being marked interrupted.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from commerce_agent.evaluation.contracts import (
    AttemptRecord,
    AttemptTelemetry,
    EvalStateConflict,
    EvalTaskStatus,
)
from commerce_agent.evaluation.episode import EpisodeExecutor, EpisodeTask
from commerce_agent.evaluation.store import EvaluationStore


class RunnerConfig(BaseModel, frozen=True, extra="forbid"):
    experiment_id: str = Field(min_length=1, max_length=128)
    purpose: str
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_list: tuple[EpisodeTask, ...] = Field(min_length=1)
    # v0.3 §16.2 spike froze 2; raised to 4 by user ruling 2026-09-16 for the
    # a-batch, conditional on the live pilot batch showing no new infra
    # failures (revert on anomaly). Same-task cross-mode stays serial below.
    concurrency: int = Field(default=2, ge=1, le=4)
    stop_grace_seconds: int = Field(default=60, ge=1)
    task_order_seed: int = 0
    compose_env_out: Path | None = None

    @model_validator(mode="after")
    def validate_same_task_serial(self) -> RunnerConfig:
        """Pit 63: the official task DB name omits the mode — same-task c/a
        episodes racing concurrently drop/create the same database."""
        if self.concurrency > 1:
            modes_per_task: dict[str, set[str]] = {}
            for task in self.task_list:
                modes_per_task.setdefault(task.task_id, set()).add(task.mode)
            if any(len(modes) > 1 for modes in modes_per_task.values()):
                raise ValueError(
                    "same task in both modes requires concurrency=1 "
                    "(official task DB naming omits the mode)"
                )
        return self


class RunSummary(BaseModel, frozen=True, extra="forbid"):
    experiment_id: str
    run_id: UUID
    status_counts: Mapping[str, int]
    attempted_episodes: int = Field(ge=0)
    completed_tasks: int = Field(ge=0)
    unfinished_tasks: int = Field(ge=0)
    stopped: bool


class EvaluationEventLog:
    """Append-only JSONL event log; one line per attempt state change."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch(exist_ok=True)

    def append(self, event: dict[str, object]) -> None:
        line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        with self._path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)

    def read_all(self) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in self._path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


class EvaluationRunner:
    def __init__(
        self,
        *,
        store: EvaluationStore,
        executor: EpisodeExecutor,
        events: EvaluationEventLog,
        config: RunnerConfig,
        run_id: UUID | None = None,
        stop_event: asyncio.Event | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._executor = executor
        self._events = events
        self._config = config
        self._run_id = run_id or uuid4()
        self._stop_event = stop_event or asyncio.Event()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._status_counts: dict[str, int] = {}

    async def run(self) -> RunSummary:
        if self._config.compose_env_out is not None:
            # pit 65: the agent container tags spool files with
            # BIRD_EXPERIMENT_ID — stage the compose env file so the stack can
            # be (re)started with the exact experiment identity
            env_path = self._config.compose_env_out
            env_path.parent.mkdir(parents=True, exist_ok=True)
            env_path.write_text(
                f"BIRD_EXPERIMENT_ID={self._config.experiment_id}\n",
                encoding="utf-8",
            )
        self._store.register_experiment(
            experiment_id=self._config.experiment_id,
            purpose=self._config.purpose,
            config_hash=self._config.config_hash,
        )
        completed = self._store.completed_tasks(self._config.experiment_id)
        next_sequence = {
            (record.task_id, record.mode): record.attempt_seq + 1
            for record in self._store.unfinished_attempts(self._config.experiment_id)
        }

        queue = self._ordered_queue(exclude=completed)
        semaphore = asyncio.Semaphore(self._config.concurrency)
        in_flight: dict[asyncio.Task[None], AttemptRecord] = {}

        for task in queue:
            if self._stop_event.is_set():
                break
            key = (task.task_id, task.mode)
            sequence = next_sequence.get(key, 1)
            next_sequence[key] = sequence + 1
            attempt = AttemptRecord(
                attempt_id=uuid4(),
                run_id=self._run_id,
                experiment_id=self._config.experiment_id,
                task_id=task.task_id,
                mode=task.mode,
                attempt_seq=sequence,
                status=EvalTaskStatus.PENDING,
                started_at=self._clock(),
            )
            episode = asyncio.create_task(
                self._run_one(task, attempt, semaphore), name=f"episode:{key}"
            )
            in_flight[episode] = attempt

        if in_flight:
            all_done = asyncio.ensure_future(
                asyncio.gather(*in_flight, return_exceptions=True)
            )
            stop_wait = asyncio.create_task(self._stop_event.wait())
            done, _pending = await asyncio.wait(
                {all_done, stop_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            if stop_wait in done:
                # stop arrived mid-flight: bounded grace, then abandon the rest
                await asyncio.wait(
                    set(in_flight), timeout=self._config.stop_grace_seconds
                )
                await self._mark_abandoned(in_flight)
                await asyncio.gather(*in_flight, return_exceptions=True)
            else:
                stop_wait.cancel()
                await all_done

        attempted = sum(self._status_counts.values())
        return RunSummary(
            experiment_id=self._config.experiment_id,
            run_id=self._run_id,
            status_counts=dict(self._status_counts),
            attempted_episodes=attempted,
            completed_tasks=len(self._store.completed_tasks(self._config.experiment_id)),
            unfinished_tasks=len(self._store.unfinished_attempts(self._config.experiment_id)),
            stopped=self._stop_event.is_set(),
        )

    def _ordered_queue(self, *, exclude: frozenset[tuple[str, str]]) -> list[EpisodeTask]:
        tasks = [task for task in self._config.task_list if (task.task_id, task.mode) not in exclude]
        ordered = sorted(tasks, key=lambda task: (task.task_id, task.mode))
        if self._config.task_order_seed:
            random.Random(self._config.task_order_seed).shuffle(ordered)
        return ordered

    async def _run_one(
        self,
        task: EpisodeTask,
        attempt: AttemptRecord,
        semaphore: asyncio.Semaphore,
    ) -> None:
        async with semaphore:
            self._store.register_attempt(attempt)
            self._events.append(self._event(attempt, "attempt_started"))
            self._store.mark_running(attempt.attempt_id, expected_status=EvalTaskStatus.PENDING)
            try:
                outcome = await self._executor.execute(task, attempt)
            except EvalStateConflict:
                raise
            except Exception:  # noqa: BLE001 -- any executor failure type is the
                # §16.1 infrastructure_error outcome; the runner must finish the
                # attempt so the task-level checkpoint stays authoritative
                outcome = self._infrastructure_outcome(attempt)
            self._store.finish_attempt(
                attempt.attempt_id,
                status=outcome.status,
                error_class=outcome.error_class,
                telemetry=outcome.telemetry,
                result=outcome.result,
            )
            self._count(outcome.status)
            self._events.append(
                self._event(attempt, "attempt_finished", status=outcome.status.value)
            )

    def _infrastructure_outcome(self, attempt: AttemptRecord):  # type: ignore[no-untyped-def]
        from commerce_agent.evaluation.episode import EpisodeOutcome

        return EpisodeOutcome(
            attempt_id=attempt.attempt_id,
            status=EvalTaskStatus.INFRASTRUCTURE_ERROR,
            error_class="executor_exception",
        )

    async def _mark_abandoned(
        self, in_flight: dict[asyncio.Task[None], AttemptRecord]
    ) -> None:
        for episode, attempt in list(in_flight.items()):
            if episode.done():
                continue
            try:
                self._store.finish_attempt(
                    attempt.attempt_id,
                    status=EvalTaskStatus.INTERRUPTED,
                    error_class=None,
                    telemetry=AttemptTelemetry(),
                )
                self._count(EvalTaskStatus.INTERRUPTED)
                self._events.append(self._event(attempt, "attempt_interrupted"))
            except EvalStateConflict:
                pass  # the episode finished inside the grace window
            episode.cancel()

    def _count(self, status: EvalTaskStatus) -> None:
        self._status_counts[status.value] = self._status_counts.get(status.value, 0) + 1

    def _event(
        self,
        attempt: AttemptRecord,
        event_type: str,
        *,
        status: str | None = None,
    ) -> dict[str, object]:
        event: dict[str, object] = {
            "recorded_at": self._clock().isoformat(),
            "run_id": str(self._run_id),
            "experiment_id": self._config.experiment_id,
            "task_id": attempt.task_id,
            "mode": attempt.mode,
            "attempt_id": str(attempt.attempt_id),
            "attempt_seq": attempt.attempt_seq,
            "event": event_type,
        }
        if status is not None:
            event["status"] = status
        return event
