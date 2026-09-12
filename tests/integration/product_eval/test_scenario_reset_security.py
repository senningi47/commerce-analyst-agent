import os
from uuid import UUID

import psycopg
import pytest
from pydantic import SecretStr

from commerce_agent.product_eval._reset import PostgresScenarioReset
from commerce_agent.trace._postgres import PostgresTraceStore
from tests.integration.trace.test_product_trace import _event

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_reset_role_has_only_fixed_reset_capability() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT "
            "has_function_privilege('product_scenario_reset', "
            " 'trusted_schema.reset_product_scenario(text)', 'EXECUTE'), "
            "has_table_privilege('product_scenario_reset', "
            " 'ops.investigation_task', 'SELECT'), "
            "has_table_privilege('product_scenario_reset', "
            " 'ops.investigation_task', 'DELETE'), "
            "has_table_privilege('product_scenario_reset', "
            " 'app.product_trace_event', 'SELECT'), "
            "has_table_privilege('product_scenario_reset', "
            " 'app.product_trace_event', 'DELETE')"
        )
        assert await cursor.fetchone() == (True, False, False, False, False)
    finally:
        await connection.rollback()
        await connection.close()


@pytest.mark.asyncio
async def test_reset_removes_only_the_exact_scenario() -> None:
    first_scenario = "task15-reset-first-v1"
    second_scenario = "task15-reset-second-v1"
    reset = PostgresScenarioReset(
        SecretStr(os.environ["PRODUCT_SCENARIO_RESET_DATABASE_DSN"])
    )
    first_trace = PostgresTraceStore(
        SecretStr(os.environ["PRODUCT_TRACE_DATABASE_DSN"]),
        scenario_id=first_scenario,
    )
    second_trace = PostgresTraceStore(
        SecretStr(os.environ["PRODUCT_TRACE_DATABASE_DSN"]),
        scenario_id=second_scenario,
    )
    first_event = _event()
    second_event = first_event.model_copy(
        update={"attempt_id": UUID("00000000-0000-0000-0000-000000000204")}
    )
    try:
        await reset.reset(first_scenario, "scenario-reset-v1")
        await reset.reset(second_scenario, "scenario-reset-v1")
        await first_trace.append(first_event)
        await second_trace.append(second_event)

        await reset.reset(first_scenario, "scenario-reset-v1")

        admin = await psycopg.AsyncConnection.connect(
            os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
        )
        try:
            cursor = await admin.execute(
                "SELECT scenario_id, count(*) FROM app.product_trace_event "
                "WHERE scenario_id = ANY(%s) GROUP BY scenario_id ORDER BY scenario_id",
                ([first_scenario, second_scenario],),
            )
            assert await cursor.fetchall() == [(second_scenario, 1)]
        finally:
            await admin.rollback()
            await admin.close()
    finally:
        await reset.reset(first_scenario, "scenario-reset-v1")
        await reset.reset(second_scenario, "scenario-reset-v1")
