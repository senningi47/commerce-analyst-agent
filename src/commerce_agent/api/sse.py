"""SSE endpoint: Last-Event-ID replay, live tail, and idle heartbeats.

The event source reads the Day 6 ``ops_read.product_trace_events`` view with
the ``agent_reader`` identity - the whitelisted view IS the §18 privacy
boundary, so the endpoint cannot over-fetch even by accident.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import SecretStr

from commerce_agent.api.events import (
    RunEventSource,
    SseRunEvent,
    format_sse_event,
)
from commerce_agent.trace.contracts import TraceDecisionSummary, TraceEvidence

_HEARTBEAT_NOTE = ": heartbeat\n\n"


class PostgresTraceEventSource:
    """Read side over ``ops_read.product_trace_events`` (agent_reader)."""

    def __init__(self, dsn: SecretStr) -> None:
        self._dsn = dsn

    async def events_after(
        self, run_id: UUID, *, after_sequence: int, limit: int = 200
    ) -> tuple[SseRunEvent, ...]:
        connection = await psycopg.AsyncConnection.connect(
            self._dsn.get_secret_value(), connect_timeout=3
        )
        try:
            cursor = await connection.execute(
                "SELECT event_cursor, run_id, attempt_id, trace_sequence, "
                "event_type, status, occurred_at, reason_code, safe_summary "
                "FROM ("
                "SELECT row_number() OVER (ORDER BY occurred_at, attempt_id, "
                "sequence) AS event_cursor, run_id, attempt_id, sequence AS "
                "trace_sequence, event_type, status, occurred_at, reason_code, "
                "safe_summary FROM ops_read.product_trace_events "
                "WHERE run_id = %s"
                ") ranked WHERE event_cursor > %s ORDER BY event_cursor LIMIT %s",
                (run_id, after_sequence, limit),
            )
            rows = await cursor.fetchall()
        finally:
            await connection.close()
        return tuple(_event_from_row(row) for row in rows)


def _event_from_row(row: tuple[Any, ...]) -> SseRunEvent:
    (
        event_cursor,
        run_id,
        attempt_id,
        trace_sequence,
        event_type,
        status,
        occurred_at,
        reason_code,
        safe_summary,
    ) = row
    summary = safe_summary or {}
    decision = summary.get("decision_summary")
    evidence = summary.get("evidence") or ()
    return SseRunEvent(
        cursor=event_cursor,
        run_id=run_id,
        attempt_id=attempt_id,
        sequence=trace_sequence,
        event_type=event_type,
        status=status,
        occurred_at=occurred_at,
        reason_code=reason_code,
        decision_summary=(
            TraceDecisionSummary.model_validate(decision) if decision else None
        ),
        proposal_ref=summary.get("proposal_ref"),
        execution_ref=summary.get("execution_ref"),
        audit_ref=summary.get("audit_ref"),
        evidence=tuple(TraceEvidence.model_validate(item) for item in evidence),
        query_fingerprints=tuple(summary.get("query_fingerprints") or ()),
        usage=summary.get("usage"),
        cost=summary.get("cost"),
    )


def parse_last_event_id(header: str | None) -> int:
    """Reconnect cursor; absent means replay the whole audit trail."""

    if header is None:
        return -1
    try:
        return int(header)
    except ValueError as error:
        raise ValueError("last-event-id must be an integer") from error


async def sse_stream(
    event_source: RunEventSource,
    run_id: UUID,
    *,
    last_event_id: int,
    poll_interval: float,
    heartbeat_interval: float,
) -> AsyncIterator[str]:
    """Replay from the cursor, tail the audit trail, heartbeat when idle."""

    last_sent = last_event_id
    last_activity = time.monotonic()
    while True:
        events = await event_source.events_after(
            run_id, after_sequence=last_sent
        )
        for event in events:
            yield format_sse_event(event)
            # codex F3: the reconnect id must be the run cursor (the same
            # field the source filters on) — advancing by the attempt-local
            # sequence re-served the tail event on every poll
            last_sent = event.cursor
            last_activity = time.monotonic()
        if not events and time.monotonic() - last_activity >= heartbeat_interval:
            yield _HEARTBEAT_NOTE
            last_activity = time.monotonic()
        await asyncio.sleep(poll_interval)


def build_events_router(
    event_source: RunEventSource,
    *,
    poll_interval: float = 1.0,
    heartbeat_interval: float = 15.0,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/runs/{run_id}/events")
    async def run_events(run_id: UUID, request: Request) -> StreamingResponse:
        try:
            last_event_id = parse_last_event_id(request.headers.get("last-event-id"))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return StreamingResponse(
            sse_stream(
                event_source,
                run_id,
                last_event_id=last_event_id,
                poll_interval=poll_interval,
                heartbeat_interval=heartbeat_interval,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    return router
