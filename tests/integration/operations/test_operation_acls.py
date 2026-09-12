import os

import psycopg
import pytest

pytestmark = pytest.mark.postgres

ROLE_DSNS = {
    "agent_reader": "PRODUCT_DATABASE_DSN",
    "proposal_writer": "PRODUCT_PROPOSAL_DATABASE_DSN",
    "approval_writer": "PRODUCT_APPROVAL_DATABASE_DSN",
    "operation_executor": "PRODUCT_OPERATION_DATABASE_DSN",
    "trace_writer": "PRODUCT_TRACE_DATABASE_DSN",
    "product_scenario_reset": "PRODUCT_SCENARIO_RESET_DATABASE_DSN",
}

FUNCTION_SIGNATURES = {
    "create_operation_proposal": (
        "trusted_schema.create_operation_proposal(uuid,integer,uuid,text,integer,jsonb,"
        "text,jsonb,jsonb,jsonb,text,timestamptz,text,text,text,text)"
    ),
    "record_approval_decision": (
        "trusted_schema.record_approval_decision(uuid,integer,uuid,uuid,text,text,text,"
        "text,text,integer,timestamptz,text,text)"
    ),
    "store_alert_backtest": (
        "trusted_schema.store_alert_backtest(uuid,text,text,jsonb,numeric,text,timestamptz)"
    ),
    "find_seller_target_candidates": (
        "trusted_schema.find_seller_target_candidates(text,timestamptz,timestamptz,"
        "numeric,bigint,text[])"
    ),
    "read_late_delivery_backtest": (
        "trusted_schema.read_late_delivery_backtest(timestamptz,timestamptz,text,"
        "bigint,text[])"
    ),
    "read_low_rating_backtest": (
        "trusted_schema.read_low_rating_backtest(timestamptz,timestamptz,text,"
        "bigint,text[])"
    ),
    "read_cancellation_backtest": (
        "trusted_schema.read_cancellation_backtest(timestamptz,timestamptz,text,"
        "bigint,text[])"
    ),
    "read_operation_proposal": (
        "trusted_schema.read_operation_proposal(uuid,integer,text)"
    ),
    "read_approval_decision": (
        "trusted_schema.read_approval_decision(uuid,integer,text)"
    ),
    "append_audit_event": (
        "trusted_schema.append_audit_event(uuid,uuid,text,integer,uuid,integer,uuid,"
        "text,text,jsonb,integer,integer,text,timestamptz,text)"
    ),
    "execute_open_seller_risk_case": (
        "trusted_schema.execute_open_seller_risk_case(uuid,uuid,integer,uuid,text,text,"
        "text,text,text,uuid,uuid,text,text,text,timestamptz,timestamptz,text,text,bigint,"
        "bigint,numeric,numeric,text,text,jsonb)"
    ),
    "execute_create_investigation_task": (
        "trusted_schema.execute_create_investigation_task(uuid,uuid,integer,uuid,text,"
        "text,text,text,text,uuid,text,text,text,text)"
    ),
    "execute_create_investigation_from_alert_hit": (
        "trusted_schema.execute_create_investigation_from_alert_hit(uuid,uuid,integer,"
        "uuid,text,text,text,text,text,uuid,uuid,text,text,text)"
    ),
    "execute_create_and_enable_metric_alert_rule": (
        "trusted_schema.execute_create_and_enable_metric_alert_rule(uuid,uuid,integer,"
        "uuid,text,text,text,text,text,uuid,uuid,text,text,text,text,text,numeric,bigint,"
        "jsonb,text)"
    ),
    "execute_assign_investigation": (
        "trusted_schema.execute_assign_investigation(uuid,uuid,integer,uuid,text,text,"
        "text,text,text,uuid,integer,text)"
    ),
    "execute_transition_investigation": (
        "trusted_schema.execute_transition_investigation(uuid,uuid,integer,uuid,text,"
        "text,text,text,text,uuid,integer,text,text,text)"
    ),
    "execute_add_investigation_conclusion": (
        "trusted_schema.execute_add_investigation_conclusion(uuid,uuid,integer,uuid,text,"
        "text,text,text,text,uuid,integer,text,text,text,jsonb)"
    ),
    "execute_close_investigation": (
        "trusted_schema.execute_close_investigation(uuid,uuid,integer,uuid,text,text,text,"
        "text,text,uuid,integer,text,text)"
    ),
    "reset_product_scenario": "trusted_schema.reset_product_scenario(text)",
}

EXPECTED_EXECUTE = {
    "agent_reader": set(),
    "proposal_writer": {
        "create_operation_proposal",
        "store_alert_backtest",
        "find_seller_target_candidates",
        "read_late_delivery_backtest",
        "read_low_rating_backtest",
        "read_cancellation_backtest",
        "read_operation_proposal",
    },
    "approval_writer": {"record_approval_decision", "read_approval_decision"},
    "operation_executor": {
        "append_audit_event",
        "execute_open_seller_risk_case",
        "execute_create_investigation_task",
        "execute_create_investigation_from_alert_hit",
        "execute_create_and_enable_metric_alert_rule",
        "execute_assign_investigation",
        "execute_transition_investigation",
        "execute_add_investigation_conclusion",
        "execute_close_investigation",
    },
    "trace_writer": set(),
    "product_scenario_reset": {"reset_product_scenario"},
}

SCHEMAS = ("trusted_schema", "ops", "app", "ops_read")

EXPECTED_SCHEMA_USAGE = {
    "agent_reader": {"ops_read"},
    "proposal_writer": {"trusted_schema"},
    "approval_writer": {"trusted_schema"},
    "operation_executor": {"trusted_schema", "ops"},
    "trace_writer": {"app"},
    "product_scenario_reset": {"trusted_schema"},
}


async def _role_connection(role: str) -> psycopg.AsyncConnection[object]:
    return await psycopg.AsyncConnection.connect(os.environ[ROLE_DSNS[role]])


@pytest.mark.asyncio
@pytest.mark.parametrize("role", tuple(ROLE_DSNS))
async def test_application_role_has_only_its_reviewed_function_family(role: str) -> None:
    connection = await _role_connection(role)
    admin = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT current_user, "
            "has_database_privilege(current_user, current_database(), 'CREATE'), "
            "has_database_privilege(current_user, current_database(), 'TEMPORARY')"
        )
        current_user, can_create, can_temp = await cursor.fetchone()
        assert current_user == role
        assert can_create is False
        assert can_temp is False

        for schema in SCHEMAS:
            cursor = await admin.execute(
                "SELECT has_schema_privilege(%s, %s, 'USAGE')",
                (role, schema),
            )
            assert (await cursor.fetchone())[0] is (
                schema in EXPECTED_SCHEMA_USAGE[role]
            )

        for name, signature in FUNCTION_SIGNATURES.items():
            cursor = await admin.execute(
                "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                (role, signature),
            )
            assert (await cursor.fetchone())[0] is (name in EXPECTED_EXECUTE[role])

        cursor = await admin.execute(
            "SELECT "
            "has_table_privilege(%s, 'ops.investigation_task', 'SELECT'), "
            "has_table_privilege(%s, 'ops.investigation_task', 'INSERT'), "
            "has_table_privilege(%s, 'ops.audit_event', 'UPDATE'), "
            "has_table_privilege(%s, 'ops.audit_event', 'DELETE'), "
            "has_table_privilege(%s, 'ops.audit_event', 'TRUNCATE')",
            (role, role, role, role, role),
        )
        assert await cursor.fetchone() == (False, False, False, False, False)
        cursor = await admin.execute(
            "SELECT "
            "has_column_privilege(%s, 'ops.command_execution', "
            " 'execution_id', 'SELECT'), "
            "has_table_privilege(%s, 'app.product_trace_event', 'INSERT'), "
            "has_table_privilege(%s, 'app.product_trace_event', 'SELECT')",
            (role, role, role),
        )
        assert await cursor.fetchone() == (
            role == "operation_executor",
            role == "trace_writer",
            False,
        )
    finally:
        await admin.rollback()
        await admin.close()
        await connection.rollback()
        await connection.close()


@pytest.mark.asyncio
async def test_owners_are_nologin_and_application_roles_have_no_membership() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT rolname, rolcanlogin FROM pg_roles "
            "WHERE rolname = ANY(%s) ORDER BY rolname",
            (["audit_owner", "ops_owner", "scenario_reset_owner"],),
        )
        assert await cursor.fetchall() == [
            ("audit_owner", False),
            ("ops_owner", False),
            ("scenario_reset_owner", False),
        ]
        cursor = await connection.execute(
            "SELECT member.rolname, granted.rolname "
            "FROM pg_auth_members AS membership "
            "JOIN pg_roles AS member ON member.oid = membership.member "
            "JOIN pg_roles AS granted ON granted.oid = membership.roleid "
            "WHERE member.rolname = ANY(%s) OR granted.rolname = ANY(%s)",
            (list(ROLE_DSNS), list(ROLE_DSNS)),
        )
        assert await cursor.fetchall() == []
    finally:
        await connection.rollback()
        await connection.close()
