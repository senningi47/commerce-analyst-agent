import os
from datetime import UTC, datetime
from uuid import UUID

import psycopg
import pytest
from pydantic import SecretStr

from commerce_agent.model.contracts import RunScope
from commerce_agent.trace._postgres import PostgresTraceStore, TraceInfrastructureError
from commerce_agent.trace.contracts import (
    ScopedTraceEvent,
    TraceDecisionSummary,
    TraceEventType,
    TraceStatus,
)

pytestmark = pytest.mark.postgres


def _event() -> ScopedTraceEvent:
    config_hash = "c" * 64
    return ScopedTraceEvent(
        run_scope=RunScope(
            run_id=UUID("00000000-0000-0000-0000-000000000201"),
            track="retail",
            mode="retail",
            subject_id="task15-postgres-integration-v1",
            experiment_id="day4-task15-postgres",
            config_hash=config_hash,
        ),
        attempt_id=UUID("00000000-0000-0000-0000-000000000202"),
        phase="proposal",
        sequence=0,
        occurred_at=datetime.now(UTC),
        node="create_proposal",
        event_type=TraceEventType.PROPOSAL_CREATED,
        status=TraceStatus.SUCCEEDED,
        decision_summary=TraceDecisionSummary(
            code="proposal_persisted",
            text="One reviewed proposal was persisted.",
        ),
        config_hash=config_hash,
        proposal_ref="proposal:00000000-0000-0000-0000-000000000203:1",
    )


@pytest.mark.asyncio
async def test_product_trace_is_append_only_and_scope_unique(
    clean_product_scenario: None,
    product_scenario_id: str,
) -> None:
    del clean_product_scenario
    store = PostgresTraceStore(
        SecretStr(os.environ["PRODUCT_TRACE_DATABASE_DSN"]),
        scenario_id=product_scenario_id,
    )
    event = _event()

    await store.append(event)
    with pytest.raises(TraceInfrastructureError) as duplicate:
        await store.append(event)
    assert duplicate.value.reason_code == "trace_database_rejected"

    admin = await psycopg.AsyncConnection.connect(os.environ["PRODUCT_POSTGRES_ADMIN_DSN"])
    try:
        cursor = await admin.execute(
            "SELECT count(*) FROM app.product_trace_event WHERE scenario_id = %s",
            (product_scenario_id,),
        )
        assert (await cursor.fetchone())[0] == 1
    finally:
        await admin.rollback()
        await admin.close()


@pytest.mark.asyncio
async def test_trace_writer_cannot_read_or_mutate_trace_and_other_domains() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_TRACE_DATABASE_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT "
            "has_table_privilege(current_user, 'app.product_trace_event', 'INSERT'), "
            "has_table_privilege(current_user, 'app.product_trace_event', 'SELECT'), "
            "has_table_privilege(current_user, 'app.product_trace_event', 'UPDATE'), "
            "has_table_privilege(current_user, 'app.product_trace_event', 'DELETE'), "
            "has_schema_privilege(current_user, 'ops', 'USAGE'), "
            "has_schema_privilege(current_user, 'retail', 'USAGE'), "
            "has_schema_privilege(current_user, 'knowledge', 'USAGE'), "
            "has_schema_privilege(current_user, 'checkpoint', 'USAGE'), "
            "has_schema_privilege(current_user, 'model_state', 'USAGE')"
        )
        assert await cursor.fetchone() == (
            True,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
        )
    finally:
        await connection.rollback()
        await connection.close()
