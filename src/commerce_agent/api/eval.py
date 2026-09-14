"""Read-only eval center API over the 0007 ops_read views (agent_reader).

The views ARE the privacy boundary: the source can only project the
whitelisted public columns, so no code path can widen the payload.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

import psycopg
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

_STATUS_ORDER = (
    "pending",
    "running",
    "succeeded",
    "failed",
    "infrastructure_error",
    "interrupted",
)


class EvalExperimentSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_id: str
    purpose: str
    config_hash: str
    created_at: datetime
    closed_at: datetime | None
    attempt_total: int
    status_counts: dict[str, int]
    reward_total: Decimal
    agent_cost_total: Decimal


class EvalAttemptRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    attempt_id: UUID
    run_id: UUID
    experiment_id: str
    task_id: str
    mode: str
    attempt_seq: int
    status: str
    error_class: str | None
    started_at: datetime
    finished_at: datetime | None
    reward: Decimal | None
    phase1_passed: bool | None
    phase2_passed: bool | None
    rounds: int | None
    tool_calls: int | None
    submit_count: int | None
    agent_cost_amount: Decimal | None
    simulator_cost_amount: Decimal | None
    agent_turns: int | None


class EvalRecordSource(Protocol):
    """Read side of the eval schema, sequenced per experiment."""

    async def experiments(self) -> tuple[EvalExperimentSummary, ...]: ...

    async def attempts(self, experiment_id: str) -> tuple[EvalAttemptRecord, ...]: ...


def _summary_from_row(row: tuple[Any, ...]) -> EvalExperimentSummary:
    (
        experiment_id,
        purpose,
        config_hash,
        created_at,
        closed_at,
        attempt_total,
        pending,
        running,
        succeeded,
        failed,
        infrastructure_error,
        interrupted,
        reward_total,
        agent_cost_total,
    ) = row
    return EvalExperimentSummary(
        experiment_id=experiment_id,
        purpose=purpose,
        config_hash=config_hash,
        created_at=created_at,
        closed_at=closed_at,
        attempt_total=attempt_total,
        status_counts=dict(
            zip(
                _STATUS_ORDER,
                (pending, running, succeeded, failed, infrastructure_error, interrupted),
                strict=True,
            )
        ),
        reward_total=reward_total,
        agent_cost_total=agent_cost_total,
    )


def _attempt_from_row(row: tuple[Any, ...]) -> EvalAttemptRecord:
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
        reward,
        phase1_passed,
        phase2_passed,
        rounds,
        tool_calls,
        submit_count,
        agent_cost_amount,
        simulator_cost_amount,
        agent_turns,
    ) = row
    return EvalAttemptRecord(
        attempt_id=attempt_id,
        run_id=run_id,
        experiment_id=experiment_id,
        task_id=task_id,
        mode=mode,
        attempt_seq=attempt_seq,
        status=status,
        error_class=error_class,
        started_at=started_at,
        finished_at=finished_at,
        reward=reward,
        phase1_passed=phase1_passed,
        phase2_passed=phase2_passed,
        rounds=rounds,
        tool_calls=tool_calls,
        submit_count=submit_count,
        agent_cost_amount=agent_cost_amount,
        simulator_cost_amount=simulator_cost_amount,
        agent_turns=agent_turns,
    )


_EXPERIMENT_SQL = (
    "SELECT e.experiment_id, e.purpose, e.config_hash, e.created_at, e.closed_at, "
    "count(a.attempt_id) AS attempt_total, "
    "count(a.attempt_id) FILTER (WHERE a.status = 'pending') AS pending, "
    "count(a.attempt_id) FILTER (WHERE a.status = 'running') AS running, "
    "count(a.attempt_id) FILTER (WHERE a.status = 'succeeded') AS succeeded, "
    "count(a.attempt_id) FILTER (WHERE a.status = 'failed') AS failed, "
    "count(a.attempt_id) FILTER (WHERE a.status = 'infrastructure_error') "
    "AS infrastructure_error, "
    "count(a.attempt_id) FILTER (WHERE a.status = 'interrupted') AS interrupted, "
    "coalesce(sum(a.reward), 0) AS reward_total, "
    "coalesce(sum(a.agent_cost_amount), 0) AS agent_cost_total "
    "FROM ops_read.eval_experiments e "
    "LEFT JOIN ops_read.eval_attempts a ON a.experiment_id = e.experiment_id "
    "GROUP BY e.experiment_id, e.purpose, e.config_hash, e.created_at, e.closed_at "
    "ORDER BY e.created_at DESC, e.experiment_id DESC LIMIT %s"
)

_ATTEMPT_SQL = (
    "SELECT attempt_id, run_id, experiment_id, task_id, mode, attempt_seq, "
    "status, error_class, started_at, finished_at, reward, phase1_passed, "
    "phase2_passed, rounds, tool_calls, submit_count, agent_cost_amount, "
    "simulator_cost_amount, agent_turns "
    "FROM ops_read.eval_attempts WHERE experiment_id = %s "
    "ORDER BY started_at, task_id, attempt_seq"
)


class PostgresEvalSource:
    """Read side over ``ops_read.eval_*`` (agent_reader)."""

    def __init__(self, dsn: Any, *, experiment_limit: int = 50) -> None:
        self._dsn = dsn
        self._experiment_limit = experiment_limit

    async def experiments(self) -> tuple[EvalExperimentSummary, ...]:
        connection = await psycopg.AsyncConnection.connect(
            self._dsn.get_secret_value(), connect_timeout=3
        )
        try:
            cursor = await connection.execute(_EXPERIMENT_SQL, (self._experiment_limit,))
            rows = await cursor.fetchall()
        finally:
            await connection.close()
        return tuple(_summary_from_row(row) for row in rows)

    async def attempts(self, experiment_id: str) -> tuple[EvalAttemptRecord, ...]:
        connection = await psycopg.AsyncConnection.connect(
            self._dsn.get_secret_value(), connect_timeout=3
        )
        try:
            cursor = await connection.execute(_ATTEMPT_SQL, (experiment_id,))
            rows = await cursor.fetchall()
        finally:
            await connection.close()
        return tuple(_attempt_from_row(row) for row in rows)


def build_eval_router(eval_source: EvalRecordSource) -> APIRouter:
    router = APIRouter()

    @router.get("/api/eval/experiments")
    async def experiments() -> list[EvalExperimentSummary]:
        return list(await eval_source.experiments())

    @router.get("/api/eval/experiments/{experiment_id}/attempts")
    async def attempts(experiment_id: str) -> list[EvalAttemptRecord]:
        return list(await eval_source.attempts(experiment_id))

    return router
