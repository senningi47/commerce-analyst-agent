"""Episode execution seam between the EvaluationRunner and one BIRD episode.

The runner owns scheduling and the task-level checkpoint; an executor owns
exactly one episode (one task, one attempt). Production uses the official
orchestrator subprocess (Task 10); tests script outcomes in-process.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from commerce_agent.evaluation.contracts import (
    AttemptRecord,
    AttemptTelemetry,
    EpisodeResult,
    EvalTaskStatus,
)


class EpisodeTask(BaseModel, frozen=True, extra="forbid"):
    task_id: str = Field(min_length=1, max_length=128)
    mode: Literal["c", "a"]


class EpisodeOutcome(BaseModel, frozen=True, extra="forbid"):
    attempt_id: UUID
    status: EvalTaskStatus
    error_class: str | None = Field(default=None, min_length=1, max_length=128)
    result: EpisodeResult | None = None
    telemetry: AttemptTelemetry = AttemptTelemetry()


class EpisodeExecutor(Protocol):
    async def execute(self, task: EpisodeTask, attempt: AttemptRecord) -> EpisodeOutcome: ...


class StubEpisodeExecutor:
    """Scripted in-process executor for runner tests; no network, no subprocess."""

    def __init__(
        self,
        script: list[EpisodeOutcome],
        *,
        fallback: Callable[[EpisodeTask, AttemptRecord], EpisodeOutcome] | None = None,
    ) -> None:
        self._script = script
        self._fallback = fallback
        self.executed: list[tuple[EpisodeTask, AttemptRecord]] = []

    async def execute(self, task: EpisodeTask, attempt: AttemptRecord) -> EpisodeOutcome:
        self.executed.append((task, attempt))
        if self._script:
            return self._script.pop(0)
        if self._fallback is not None:
            return self._fallback(task, attempt)
        raise AssertionError("stub executor script exhausted")
