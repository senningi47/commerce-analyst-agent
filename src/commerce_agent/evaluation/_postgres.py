"""Role-bound PostgreSQL evaluation store writing the `eval` schema.

The DSN comes from the reviewed `PRODUCT_EVALUATION_DATABASE_DSN` env value
(consumed via `uv run --env-file .env`, never logged). Every operation runs in
its own autocommit transaction; optimistic guards return rowcount zero on
stale states, which becomes `EvalStateConflict` instead of an overwrite.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg

from commerce_agent.evaluation.contracts import (
    AttemptRecord,
    AttemptTelemetry,
    EpisodeResult,
    EvalStateConflict,
    EvalTaskStatus,
)

_TERMINAL_SQL = "('succeeded','failed')"
_RETRYABLE_SQL = "('pending','running','infrastructure_error','interrupted')"


def _telemetry_json(telemetry: AttemptTelemetry) -> dict[str, Any]:
    return json.loads(telemetry.model_dump_json())


def _record_from_row(row: tuple[Any, ...]) -> AttemptRecord:
    (
        attempt_id,
        run_id,
        experiment_id,
        task_id,
        mode,
        attempt_seq,
        status,
        error_class,
        started_at,
        finished_at,
        telemetry,
    ) = row
    return AttemptRecord(
        attempt_id=attempt_id,
        run_id=run_id,
        experiment_id=experiment_id,
        task_id=task_id,
        mode=mode,
        attempt_seq=attempt_seq,
        status=EvalTaskStatus(status),
        error_class=error_class,
        started_at=started_at,
        finished_at=finished_at,
        telemetry=AttemptTelemetry.model_validate(telemetry or {}),
    )


class PostgresEvaluationStore:
    def __init__(self, *, dsn: str) -> None:
        if not dsn:
            raise ValueError("evaluation store DSN must not be empty")
        self._dsn = dsn

    def _execute(self, statement: Any, params: tuple[Any, ...] | None = None) -> int:
        with (
            psycopg.connect(self._dsn, autocommit=True) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, params)
            return cursor.rowcount

    def _execute_returning(
        self, statement: Any, params: tuple[Any, ...] | None = None
    ) -> list[tuple[Any, ...]]:
        with (
            psycopg.connect(self._dsn, autocommit=True) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, params)
            rows = cursor.fetchall()
        return list(rows)

    def register_experiment(
        self, *, experiment_id: str, purpose: str, config_hash: str
    ) -> None:
        inserted = self._execute(
            "INSERT INTO eval.experiment (experiment_id, purpose, config_hash) "
            "VALUES (%s, %s, %s) ON CONFLICT (experiment_id) DO NOTHING",
            (experiment_id, purpose, config_hash),
        )
        if inserted == 1:
            return
        rows = self._execute_returning(
            "SELECT purpose, config_hash FROM eval.experiment WHERE experiment_id = %s",
            (experiment_id,),
        )
        existing = rows[0] if rows else None
        if existing != (purpose, config_hash):
            raise EvalStateConflict("experiment_identity_conflict")

    def register_attempt(self, record: AttemptRecord) -> None:
        try:
            self._execute(
                "INSERT INTO eval.task_attempt (attempt_id, run_id, experiment_id, "
                "task_id, mode, attempt_seq, status, error_class, started_at, telemetry) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    record.attempt_id,
                    record.run_id,
                    record.experiment_id,
                    record.task_id,
                    record.mode,
                    record.attempt_seq,
                    record.status.value,
                    record.error_class,
                    record.started_at,
                    _telemetry_json(record.telemetry),
                ),
            )
        except psycopg.errors.UniqueViolation as error:
            raise EvalStateConflict("attempt_identity_conflict") from error

    def mark_running(self, attempt_id: UUID, *, expected_status: EvalTaskStatus) -> None:
        if expected_status != EvalTaskStatus.PENDING:
            raise EvalStateConflict("status_transition_conflict")
        rowcount = self._execute(
            "UPDATE eval.task_attempt SET status = 'running' "
            "WHERE attempt_id = %s AND status = 'pending'",
            (attempt_id,),
        )
        if rowcount != 1:
            raise EvalStateConflict("status_transition_conflict")

    def finish_attempt(
        self,
        attempt_id: UUID,
        *,
        status: EvalTaskStatus,
        error_class: str | None,
        telemetry: AttemptTelemetry,
        result: EpisodeResult | None = None,
    ) -> None:
        """Terminal transition and optional result row inside ONE transaction."""

        with (
            psycopg.connect(self._dsn, autocommit=False) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE eval.task_attempt SET status = %s, error_class = %s, "
                "finished_at = now(), telemetry = %s "
                "WHERE attempt_id = %s AND status = 'running'",
                (status.value, error_class, _telemetry_json(telemetry), attempt_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise EvalStateConflict("status_transition_conflict")
            if result is not None:
                cursor.execute(
                    "INSERT INTO eval.task_result (attempt_id, reward, "
                    "phase1_passed, phase2_passed, rounds, tool_calls, submit_count) "
                    "SELECT %s, %s, %s, %s, %s, %s, %s FROM eval.task_attempt "
                    "WHERE attempt_id = %s AND status = 'succeeded' "
                    "ON CONFLICT (attempt_id) DO NOTHING",
                    (
                        attempt_id,
                        result.reward
                        if result.reward is None
                        else Decimal(result.reward),
                        result.phase1_passed,
                        result.phase2_passed,
                        result.rounds,
                        result.tool_calls,
                        result.submit_count,
                        attempt_id,
                    ),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    raise EvalStateConflict("result_requires_succeeded_attempt")
            connection.commit()

    def completed_tasks(self, experiment_id: str) -> frozenset[tuple[str, str]]:
        rows = self._execute_returning(
            "SELECT task_id, mode FROM eval.task_attempt "
            "WHERE experiment_id = %s AND status IN " + _TERMINAL_SQL,
            (experiment_id,),
        )
        return frozenset((str(task_id), str(mode)) for task_id, mode in rows)

    def unfinished_attempts(self, experiment_id: str) -> tuple[AttemptRecord, ...]:
        rows = self._execute_returning(
            "SELECT DISTINCT ON (task_id, mode) attempt_id, run_id, experiment_id, "
            "task_id, mode, attempt_seq, status, error_class, started_at, finished_at, "
            "telemetry FROM eval.task_attempt "
            "WHERE experiment_id = %s AND status IN " + _RETRYABLE_SQL + " "
            "AND NOT EXISTS ("
            "SELECT 1 FROM eval.task_attempt AS terminal "
            "WHERE terminal.experiment_id = eval.task_attempt.experiment_id "
            "AND terminal.task_id = eval.task_attempt.task_id "
            "AND terminal.mode = eval.task_attempt.mode "
            "AND terminal.status IN " + _TERMINAL_SQL + ") "
            "ORDER BY task_id, mode, attempt_seq DESC",
            (experiment_id,),
        )
        return tuple(_record_from_row(row) for row in rows)

    def record_result(self, attempt_id: UUID, result: EpisodeResult) -> None:
        rowcount = self._execute(
            "INSERT INTO eval.task_result (attempt_id, reward, phase1_passed, "
            "phase2_passed, rounds, tool_calls, submit_count) "
            "SELECT %s, %s, %s, %s, %s, %s, %s FROM eval.task_attempt "
            "WHERE attempt_id = %s AND status = 'succeeded' "
            "ON CONFLICT (attempt_id) DO NOTHING",
            (
                attempt_id,
                result.reward if result.reward is None else Decimal(result.reward),
                result.phase1_passed,
                result.phase2_passed,
                result.rounds,
                result.tool_calls,
                result.submit_count,
                attempt_id,
            ),
        )
        if rowcount != 1:
            raise EvalStateConflict("result_requires_succeeded_attempt")

    def result(self, attempt_id: UUID) -> EpisodeResult | None:
        rows = self._execute_returning(
            "SELECT reward, phase1_passed, phase2_passed, rounds, tool_calls, submit_count "
            "FROM eval.task_result WHERE attempt_id = %s",
            (attempt_id,),
        )
        if not rows:
            return None
        reward, phase1, phase2, rounds, tool_calls, submit_count = rows[0]
        return EpisodeResult(
            reward=reward,
            phase1_passed=phase1,
            phase2_passed=phase2,
            rounds=rounds,
            tool_calls=tool_calls,
            submit_count=submit_count,
        )
