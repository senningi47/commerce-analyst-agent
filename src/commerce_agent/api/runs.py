"""Run directory: discover auditable run ids for the workbench live mode."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

import psycopg
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict


class RunSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    started_at: datetime
    last_event_at: datetime
    event_count: int


class RunDirectory(Protocol):
    """Read side over the product trace run identities."""

    async def runs(self) -> tuple[RunSummary, ...]: ...


_RUNS_SQL = (
    "SELECT run_id, min(occurred_at) AS started_at, "
    "max(occurred_at) AS last_event_at, count(*) AS event_count "
    "FROM ops_read.product_trace_events GROUP BY run_id "
    "ORDER BY max(occurred_at) DESC LIMIT %s"
)


class PostgresRunDirectory:
    """Read side over ``ops_read.product_trace_events`` (agent_reader)."""

    def __init__(self, dsn: Any, *, run_limit: int = 50) -> None:
        self._dsn = dsn
        self._run_limit = run_limit

    async def runs(self) -> tuple[RunSummary, ...]:
        connection = await psycopg.AsyncConnection.connect(
            self._dsn.get_secret_value(), connect_timeout=3
        )
        try:
            cursor = await connection.execute(_RUNS_SQL, (self._run_limit,))
            rows = await cursor.fetchall()
        finally:
            await connection.close()
        return tuple(
            RunSummary(
                run_id=row[0],
                started_at=row[1],
                last_event_at=row[2],
                event_count=row[3],
            )
            for row in rows
        )


def build_runs_router(run_directory: RunDirectory) -> APIRouter:
    router = APIRouter()

    @router.get("/api/runs")
    async def runs() -> list[RunSummary]:
        return list(await run_directory.runs())

    return router
