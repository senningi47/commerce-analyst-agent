"""Evaluation store protocol: the task-level checkpoint surface (v0.3 §16.1).

Every method is a single atomic operation. Status transitions carry an
expected-status guard so concurrent or stale workers fail closed through
`EvalStateConflict` instead of overwriting each other. `finish_attempt`
atomically applies the terminal transition and the public result when one is
present, so a crash can never leave a succeeded attempt without its result.
"""

from typing import Protocol
from uuid import UUID

from commerce_agent.evaluation.contracts import (
    AttemptRecord,
    AttemptTelemetry,
    EpisodeResult,
    EvalTaskStatus,
)


class EvaluationStore(Protocol):
    def register_experiment(
        self, *, experiment_id: str, purpose: str, config_hash: str
    ) -> None:
        """Idempotently create the experiment; conflict on differing identity."""
        ...

    def register_attempt(self, record: AttemptRecord) -> None:
        """Insert one attempt; conflict on duplicate attempt or identity."""
        ...

    def mark_running(self, attempt_id: UUID, *, expected_status: EvalTaskStatus) -> None:
        """Any -> running guarded by the expected previous status."""
        ...

    def finish_attempt(
        self,
        attempt_id: UUID,
        *,
        status: EvalTaskStatus,
        error_class: str | None,
        telemetry: AttemptTelemetry,
        result: EpisodeResult | None = None,
    ) -> None:
        """running -> terminal status; atomically records `result` when given."""
        ...

    def completed_tasks(self, experiment_id: str) -> frozenset[tuple[str, str]]:
        """(task_id, mode) pairs with a terminal succeeded/failed attempt."""
        ...

    def merge_telemetry(self, attempt_id: UUID, patch: dict[str, object]) -> None:
        """Day 6 spool import: jsonb-merge agent usage/cost into telemetry."""
        ...

    def unfinished_attempts(self, experiment_id: str) -> tuple[AttemptRecord, ...]:
        """Latest non-terminal attempt per (task_id, mode) not yet completed."""
        ...

    def record_result(self, attempt_id: UUID, result: EpisodeResult) -> None:
        """Insert the public result; only succeeded attempts may carry one."""
        ...
