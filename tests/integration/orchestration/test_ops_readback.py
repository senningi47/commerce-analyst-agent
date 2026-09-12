import os

import psycopg
import pytest

pytestmark = pytest.mark.postgres

VIEW_COLUMNS = {
    "investigation_tasks": (
        "task_ref",
        "status",
        "assignee_ref",
        "version",
        "evidence_summary",
    ),
    "risk_annotations": (
        "risk_ref",
        "seller_ref",
        "status",
        "observed_from",
        "observed_to",
        "metric_ref",
        "metric_value",
    ),
    "enabled_metric_alert_rules": (
        "rule_ref",
        "metric_ref",
        "metric_revision",
        "grain",
        "comparator",
        "threshold",
        "window",
        "version",
    ),
    "enabled_metric_alert_hits": (
        "hit_ref",
        "rule_ref",
        "window_start",
        "window_end",
        "value",
        "coverage",
    ),
}


@pytest.mark.asyncio
async def test_agent_reader_sees_only_reviewed_ops_read_columns() -> None:
    connection = await psycopg.AsyncConnection.connect(os.environ["PRODUCT_DATABASE_DSN"])
    try:
        for view, expected_columns in VIEW_COLUMNS.items():
            cursor = await connection.execute(f"SELECT * FROM ops_read.{view} LIMIT 0")
            assert cursor.description is not None
            assert tuple(column.name for column in cursor.description) == expected_columns
        assert "seller_id" not in VIEW_COLUMNS["risk_annotations"]
        assert "seller_digest" not in VIEW_COLUMNS["risk_annotations"]
    finally:
        await connection.rollback()
        await connection.close()


@pytest.mark.asyncio
async def test_agent_reader_cannot_access_raw_operation_audit_or_trace_tables() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT "
            "has_table_privilege('agent_reader', "
            " 'ops.operation_proposal', 'SELECT'), "
            "has_table_privilege('agent_reader', "
            " 'ops.investigation_task', 'SELECT'), "
            "has_table_privilege('agent_reader', 'ops.audit_event', 'SELECT'), "
            "has_table_privilege('agent_reader', "
            " 'app.product_trace_event', 'SELECT')"
        )
        assert await cursor.fetchone() == (False, False, False, False)
    finally:
        await connection.rollback()
        await connection.close()
