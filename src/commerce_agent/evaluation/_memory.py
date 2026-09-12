"""Lock-protected in-memory evaluation store for unit and stub end-to-end runs."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from commerce_agent.evaluation.contracts import (
    TERMINAL_STATUSES,
    AttemptRecord,
    AttemptTelemetry,
    EpisodeResult,
    EvalStateConflict,
    EvalTaskStatus,
)


class InMemoryEvaluationStore:
    """Deterministic evaluation store; one lock guards every transition."""

    def __init__(
        self, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._experiments: dict[str, tuple[str, str]] = {}
        self._attempts: dict[UUID, AttemptRecord] = {}
        self._results: dict[UUID, EpisodeResult] = {}

    def register_experiment(
        self, *, experiment_id: str, purpose: str, config_hash: str
    ) -> None:
        with self._lock:
            existing = self._experiments.get(experiment_id)
            if existing is not None:
                if existing != (purpose, config_hash):
                    raise EvalStateConflict("experiment_identity_conflict")
                return
            self._experiments[experiment_id] = (purpose, config_hash)

    def register_attempt(self, record: AttemptRecord) -> None:
        with self._lock:
            if record.attempt_id in self._attempts:
                raise EvalStateConflict("attempt_identity_conflict")
            for existing in self._attempts.values():
                if (
                    existing.run_id == record.run_id
                    and existing.task_id == record.task_id
                    and existing.mode == record.mode
                    and existing.attempt_seq == record.attempt_seq
                ):
                    raise EvalStateConflict("attempt_sequence_conflict")
            self._attempts[record.attempt_id] = record

    def mark_running(self, attempt_id: object, *, expected_status: EvalTaskStatus) -> None:
        with self._lock:
            record = self._attempts.get(attempt_id)  # type: ignore[arg-type]
            if record is None or record.status != expected_status:
                raise EvalStateConflict("status_transition_conflict")
            self._attempts[record.attempt_id] = record.model_copy(
                update={"status": EvalTaskStatus.RUNNING}
            )

    def finish_attempt(
        self,
        attempt_id: object,
        *,
        status: EvalTaskStatus,
        error_class: str | None,
        telemetry: AttemptTelemetry,
        result: EpisodeResult | None = None,
    ) -> None:
        with self._lock:
            record = self._attempts.get(attempt_id)  # type: ignore[arg-type]
            if record is None or record.status != EvalTaskStatus.RUNNING:
                raise EvalStateConflict("status_transition_conflict")
            if error_class is not None and status == EvalTaskStatus.SUCCEEDED:
                raise EvalStateConflict("succeeded_requires_no_error_class")
            if result is not None and status != EvalTaskStatus.SUCCEEDED:
                raise EvalStateConflict("result_requires_succeeded_attempt")
            if result is not None and attempt_id in self._results:  # type: ignore[arg-type]
                raise EvalStateConflict("result_already_recorded")
            self._attempts[record.attempt_id] = record.model_copy(
                update={
                    "status": status,
                    "error_class": error_class,
                    "finished_at": self._clock(),
                    "telemetry": telemetry,
                }
            )
            if result is not None:
                self._results[attempt_id] = result  # type: ignore[arg-type]

    def completed_tasks(self, experiment_id: str) -> frozenset[tuple[str, str]]:
        with self._lock:
            return frozenset(
                (record.task_id, record.mode)
                for record in self._attempts.values()
                if record.experiment_id == experiment_id
                and record.status in TERMINAL_STATUSES
            )

    def unfinished_attempts(self, experiment_id: str) -> tuple[AttemptRecord, ...]:
        completed = self.completed_tasks(experiment_id)
        with self._lock:
            latest: dict[tuple[str, str], AttemptRecord] = {}
            for record in self._attempts.values():
                if record.experiment_id != experiment_id:
                    continue
                if (record.task_id, record.mode) in completed:
                    continue
                key = (record.task_id, record.mode)
                if key not in latest or record.attempt_seq > latest[key].attempt_seq:
                    latest[key] = record
            return tuple(
                sorted(latest.values(), key=lambda record: (record.task_id, record.mode))
            )

    def record_result(self, attempt_id: object, result: EpisodeResult) -> None:
        with self._lock:
            record = self._attempts.get(attempt_id)  # type: ignore[arg-type]
            if record is None or record.status != EvalTaskStatus.SUCCEEDED:
                raise EvalStateConflict("result_requires_succeeded_attempt")
            if attempt_id in self._results:  # type: ignore[arg-type]
                raise EvalStateConflict("result_already_recorded")
            self._results[attempt_id] = result  # type: ignore[arg-type]

    def result(self, attempt_id: UUID) -> EpisodeResult | None:
        with self._lock:
            return self._results.get(attempt_id)

    def experiment(self, experiment_id: str) -> tuple[str, str] | None:
        with self._lock:
            return self._experiments.get(experiment_id)

    def attempt(self, attempt_id: UUID) -> AttemptRecord | None:
        with self._lock:
            return self._attempts.get(attempt_id)

    def attempt_count(self) -> int:
        with self._lock:
            return len(self._attempts)
