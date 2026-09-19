import json
import os

import psycopg
import pytest
from pydantic import SecretStr

from commerce_agent.query_engine._ast_policy import ValidatedQuery
from commerce_agent.query_engine._postgres import (
    PostgresExecutor,
    _begin_guarded_transaction,
    _enforce_plan_limits,
    _extract_explain_summary,
)
from commerce_agent.query_engine.contracts import ExplainSummary
from commerce_agent.query_engine.errors import (
    SqlExecutionError,
    SqlPlanRejected,
    SqlResultLimitExceeded,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def executor() -> PostgresExecutor:
    return PostgresExecutor(SecretStr(os.environ["PRODUCT_DATABASE_DSN"]))


@pytest.mark.asyncio
async def test_guarded_transaction_applies_read_only_gucs() -> None:
    expected = {
        "transaction_read_only": "on",
        "statement_timeout": "5s",
        "lock_timeout": "1s",
        "idle_in_transaction_session_timeout": "10s",
        "work_mem": "16MB",
        "max_parallel_workers_per_gather": "0",
        "search_path": "retail, ops_read",
    }
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_DATABASE_DSN"]
    )
    try:
        await _begin_guarded_transaction(connection)
        for setting_name, expected_value in expected.items():
            cursor = await connection.execute(
                "SELECT current_setting(%s)",
                (setting_name,),
            )
            assert (await cursor.fetchone())[0] == expected_value
    finally:
        await connection.rollback()
        await connection.close()


@pytest.mark.asyncio
async def test_safe_query_returns_rows_and_explain(
    executor: PostgresExecutor,
) -> None:
    result = await executor.execute(
        ValidatedQuery(
            sql=(
                "SELECT order_id FROM retail.orders "
                "ORDER BY order_id LIMIT 2"
            ),
            is_aggregate=False,
        )
    )

    assert result.columns == ["order_id"]
    assert result.row_count == 2
    assert result.explain is not None
    assert result.explain.total_cost >= 0
    assert result.explain.plan_rows >= 0


@pytest.mark.parametrize(
    ("summary", "reason_code"),
    [
        (ExplainSummary(total_cost=1_000_001, plan_rows=1), "plan_cost"),
        (ExplainSummary(total_cost=1, plan_rows=1_000_001), "plan_rows"),
    ],
)
def test_plan_limits_reject_expensive_plans(
    summary: ExplainSummary,
    reason_code: str,
) -> None:
    with pytest.raises(SqlPlanRejected) as captured:
        _enforce_plan_limits(summary)

    assert captured.value.reason_code == reason_code


@pytest.mark.parametrize(
    "payload",
    [
        [{"Plan": {"Total Cost": 12.5, "Plan Rows": 3}}],
        json.dumps([{"Plan": {"Total Cost": 12.5, "Plan Rows": 3}}]),
    ],
)
def test_extract_explain_summary_accepts_decoded_or_json_payload(
    payload: object,
) -> None:
    assert _extract_explain_summary(payload) == ExplainSummary(
        total_cost=12.5,
        plan_rows=3,
    )


@pytest.mark.asyncio
async def test_executor_rejects_more_than_1000_rows(
    executor: PostgresExecutor,
) -> None:
    with pytest.raises(SqlResultLimitExceeded) as captured:
        await executor.execute(
            ValidatedQuery(
                sql="SELECT generate_series(1, 1001) AS n",
                is_aggregate=False,
            )
        )

    assert captured.value.reason_code == "row_limit"


@pytest.mark.asyncio
async def test_executor_rejects_more_than_five_megabytes(
    executor: PostgresExecutor,
) -> None:
    with pytest.raises(SqlResultLimitExceeded) as captured:
        await executor.execute(
            ValidatedQuery(
                sql="SELECT repeat('x', 5242881) AS oversized",
                is_aggregate=False,
            )
        )

    assert captured.value.reason_code == "byte_limit"


@pytest.mark.asyncio
async def test_executor_rejects_duplicate_columns(
    executor: PostgresExecutor,
) -> None:
    with pytest.raises(SqlExecutionError) as captured:
        await executor.execute(
            ValidatedQuery(
                sql="SELECT 1 AS duplicate, 2 AS duplicate",
                is_aggregate=False,
            )
        )

    assert captured.value.reason_code == "duplicate_columns"


@pytest.mark.parametrize(
    ("sql", "expected_sqlstate"),
    [
        ("SELECT missing_column FROM retail.orders LIMIT 1", "42703"),
        ("SELECT pg_sleep(6)", "57014"),
    ],
)
@pytest.mark.asyncio
async def test_executor_sanitizes_postgres_errors(
    executor: PostgresExecutor,
    sql: str,
    expected_sqlstate: str,
) -> None:
    with pytest.raises(SqlExecutionError) as captured:
        await executor.execute(ValidatedQuery(sql=sql, is_aggregate=False))

    error = captured.value
    reader_dsn = os.environ["PRODUCT_DATABASE_DSN"]
    assert error.reason_code == "postgres_error"
    assert error.sqlstate == expected_sqlstate
    assert reader_dsn not in str(error)
    assert reader_dsn not in repr(error)
