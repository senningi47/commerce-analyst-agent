"""PG gate: the SSE push sequence matches the product closed-loop audit.

Day 6 Task 7 acceptance: drive a full closed loop's trace events through the
real PostgresTraceStore, then consume the SSE generator with the real
ops_read-backed source and assert the pushed sequence IS the audit sequence -
across proposal-pending, rejection, repair, and recovery states - through the
whitelisted ``agent_reader`` view.
"""

import os
from datetime import UTC, datetime
from uuid import UUID

import psycopg
import pytest
from pydantic import SecretStr

from commerce_agent.api.sse import PostgresTraceEventSource, sse_stream
from commerce_agent.model.contracts import RunScope
from commerce_agent.product_eval._reset import PostgresScenarioReset
from commerce_agent.trace._postgres import PostgresTraceStore
from commerce_agent.trace.contracts import (
    ScopedTraceEvent,
    TraceDecisionSummary,
    TraceEventType,
    TraceStatus,
)

pytestmark = pytest.mark.postgres

SCENARIO_ID = "task15-api-sse-v1"
RUN_ID = UUID("00000000-0000-0000-0000-000000000301")
ATTEMPT_ONE = UUID("00000000-0000-0000-0000-000000000302")
ATTEMPT_TWO = UUID("00000000-0000-0000-0000-000000000303")

LOOP_SEQUENCE = (
    ("clarification_requested", "clarify"),
    ("investigation_plan_accepted", "plan"),
    ("sql_generated", "sql_generate"),
    ("sql_repaired", "sql_repair"),
    ("query_executed", "execute_query"),
    ("query_reconciled", "reconcile"),
    ("proposal_created", "propose"),
    ("decision_recorded", "decide"),
    ("proposal_created", "propose"),
    ("decision_recorded", "decide"),
    ("execution_completed", "execute_operation"),
    ("report_completed", "report"),
)


def _event(
    attempt_id: UUID,
    sequence: int,
    event_type: TraceEventType,
    node: str,
    *,
    decision_summary: TraceDecisionSummary | None = None,
    proposal_ref: str | None = None,
    execution_ref: str | None = None,
) -> ScopedTraceEvent:
    config_hash = "c" * 64
    return ScopedTraceEvent(
        run_scope=RunScope(
            run_id=RUN_ID,
            track="retail",
            mode="retail",
            subject_id=SCENARIO_ID,
            experiment_id="day6-api-sse",
            config_hash=config_hash,
        ),
        attempt_id=attempt_id,
        phase="closed_loop",
        sequence=sequence,
        occurred_at=datetime.now(UTC),
        node=node,
        event_type=event_type,
        status=TraceStatus.SUCCEEDED,
        decision_summary=decision_summary,
        config_hash=config_hash,
        proposal_ref=proposal_ref,
        execution_ref=execution_ref,
    )


async def _write_loop() -> None:
    store = PostgresTraceStore(
        SecretStr(os.environ["PRODUCT_TRACE_DATABASE_DSN"]), scenario_id=SCENARIO_ID
    )
    events = []
    for sequence, (event_type, node) in enumerate(LOOP_SEQUENCE):
        decision_summary = None
        proposal_ref = None
        execution_ref = None
        if event_type == "decision_recorded":
            rejected = sequence == 7
            decision_summary = TraceDecisionSummary(
                code="proposal_rejected" if rejected else "proposal_approved",
                text=(
                    "The reviewer requested changes."
                    if rejected
                    else "The reviewer approved the proposal."
                ),
            )
        if sequence == 6:
            proposal_ref = "proposal:00000000-0000-0000-0000-000000000311:1"
        if sequence == 8:
            proposal_ref = "proposal:00000000-0000-0000-0000-000000000311:2"
        if sequence == 10:
            execution_ref = "execution:00000000-0000-0000-0000-000000000312"
        events.append(
            _event(
                ATTEMPT_ONE,
                sequence,
                TraceEventType(event_type),
                node,
                decision_summary=decision_summary,
                proposal_ref=proposal_ref,
                execution_ref=execution_ref,
            )
        )
    events.append(_event(ATTEMPT_TWO, 0, TraceEventType.RECOVERY_APPLIED, "recover"))
    for event in events:
        await store.append(event)


async def _take_chunks(stream: object, count: int) -> list[str]:
    chunks: list[str] = []
    async for chunk in stream:
        chunks.append(chunk)
        if len(chunks) >= count:
            break
    return chunks


def _id_of(chunk: str) -> int:
    return int(next(line for line in chunk.splitlines() if line.startswith("id: "))[4:])


@pytest.fixture
async def clean_loop() -> None:
    reset = PostgresScenarioReset(
        SecretStr(os.environ["PRODUCT_SCENARIO_RESET_DATABASE_DSN"])
    )
    await reset.reset(SCENARIO_ID, "scenario-reset-v1")
    await _write_loop()
    yield
    await reset.reset(SCENARIO_ID, "scenario-reset-v1")


@pytest.mark.asyncio
async def test_sse_push_sequence_matches_the_closed_loop_audit(
    clean_loop: None,
) -> None:
    del clean_loop
    source = PostgresTraceEventSource(
        SecretStr(os.environ["PRODUCT_DATABASE_DSN"])
    )

    chunks = await _take_chunks(
        sse_stream(source, RUN_ID, last_event_id=-1, poll_interval=60.0,
                   heartbeat_interval=3600.0),
        13,
    )

    ids = [_id_of(chunk) for chunk in chunks]
    assert ids == list(range(1, 14))  # run-scoped cursor: 12 + recovery
    assert "event: clarification_requested" in chunks[0]
    assert "event: sql_repaired" in chunks[3]
    assert "event: proposal_created" in chunks[6]
    assert "event: decision_recorded" in chunks[7]
    assert "event: recovery_applied" in chunks[12]
    data_seven = next(
        line for line in chunks[7].splitlines() if line.startswith("data: ")
    )
    assert "proposal_rejected" in data_seven
    assert "The reviewer requested changes." in data_seven


@pytest.mark.asyncio
async def test_last_event_id_resume_only_pushes_later_audit_events(
    clean_loop: None,
) -> None:
    del clean_loop
    source = PostgresTraceEventSource(
        SecretStr(os.environ["PRODUCT_DATABASE_DSN"])
    )

    chunks = await _take_chunks(
        sse_stream(source, RUN_ID, last_event_id=6, poll_interval=60.0,
                   heartbeat_interval=3600.0),
        7,
    )

    assert [_id_of(chunk) for chunk in chunks] == [7, 8, 9, 10, 11, 12, 13]


@pytest.mark.asyncio
async def test_view_exposes_only_the_whitelisted_public_columns() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_DATABASE_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT * FROM ops_read.product_trace_events LIMIT 0"
        )
        assert cursor.description is not None
        assert [column.name for column in cursor.description] == [
            "run_id",
            "attempt_id",
            "sequence",
            "event_type",
            "status",
            "safe_summary",
            "reason_code",
            "occurred_at",
        ]
    finally:
        await connection.rollback()
        await connection.close()
