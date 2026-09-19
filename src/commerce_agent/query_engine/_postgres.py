import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import psycopg
from pydantic import SecretStr

from commerce_agent.query_engine._ast_policy import ValidatedQuery
from commerce_agent.query_engine.contracts import (
    ExplainSummary,
    QueryResult,
    QueryScalar,
)
from commerce_agent.query_engine.errors import (
    QueryInfrastructureError,
    SqlExecutionError,
    SqlPlanRejected,
    SqlResultLimitExceeded,
)

SET_LOCAL_STATEMENTS = (
    "SET LOCAL statement_timeout = '5s'",
    "SET LOCAL lock_timeout = '1s'",
    "SET LOCAL idle_in_transaction_session_timeout = '10s'",
    "SET LOCAL work_mem = '16MB'",
    "SET LOCAL max_parallel_workers_per_gather = 0",
    "SET LOCAL search_path = retail, ops_read",
)


async def _begin_guarded_transaction(
    connection: psycopg.AsyncConnection[object],
) -> None:
    await connection.execute("BEGIN READ ONLY")
    for statement in SET_LOCAL_STATEMENTS:
        await connection.execute(statement)


def _normalize_scalar(value: object) -> QueryScalar:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date | datetime | UUID):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    raise TypeError(f"Unsupported query result type: {type(value).__name__}")


def _extract_explain_summary(payload: object) -> ExplainSummary:
    try:
        decoded = json.loads(payload) if isinstance(payload, str) else payload
    except json.JSONDecodeError as error:
        raise QueryInfrastructureError(
            "explain_shape",
            "Unexpected EXPLAIN payload",
        ) from error
    if not isinstance(decoded, list) or len(decoded) != 1:
        raise QueryInfrastructureError(
            "explain_shape",
            "Unexpected EXPLAIN payload",
        )
    document = decoded[0]
    if not isinstance(document, dict) or not isinstance(document.get("Plan"), dict):
        raise QueryInfrastructureError(
            "explain_shape",
            "Unexpected EXPLAIN payload",
        )
    plan = document["Plan"]
    try:
        return ExplainSummary(
            total_cost=float(plan["Total Cost"]),
            plan_rows=int(plan["Plan Rows"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise QueryInfrastructureError(
            "explain_shape",
            "Unexpected EXPLAIN payload",
        ) from error


def _enforce_plan_limits(summary: ExplainSummary) -> None:
    if summary.total_cost > 1_000_000:
        raise SqlPlanRejected(
            "plan_cost",
            "Query plan cost exceeds the development limit",
        )
    if summary.plan_rows > 1_000_000:
        raise SqlPlanRejected(
            "plan_rows",
            "Query plan rows exceed the development limit",
        )


class PostgresExecutor:
    def __init__(self, dsn: SecretStr) -> None:
        self._dsn = dsn

    async def execute(self, query: ValidatedQuery) -> QueryResult:
        try:
            connection = await psycopg.AsyncConnection.connect(
                self._dsn.get_secret_value(),
                application_name="commerce_query_engine",
                connect_timeout=3,
            )
        except psycopg.Error as error:
            raise QueryInfrastructureError(
                "connection_failed",
                "Product database connection failed",
            ) from error

        try:
            await _begin_guarded_transaction(connection)
            explain_cursor = await connection.execute("EXPLAIN (FORMAT JSON) " + query.sql)
            explain_row = await explain_cursor.fetchone()
            if explain_row is None:
                raise QueryInfrastructureError(
                    "explain_shape",
                    "Unexpected EXPLAIN payload",
                )
            summary = _extract_explain_summary(explain_row[0])
            _enforce_plan_limits(summary)

            async with connection.cursor(name="product_query_stream") as cursor:
                await cursor.execute(query.sql)
                description = cursor.description
                if description is None:
                    raise QueryInfrastructureError(
                        "result_shape",
                        "Query did not return a result set",
                    )
                columns = [column.name for column in description]
                if len(columns) != len(set(columns)):
                    raise SqlExecutionError(
                        "duplicate_columns",
                        "Query result column names must be unique",
                    )
                rows: list[dict[str, QueryScalar]] = []
                serialized_bytes = 0
                async for raw_row in cursor:
                    if len(rows) >= 1000:
                        raise SqlResultLimitExceeded(
                            "row_limit",
                            "Query returned more than 1000 rows",
                        )
                    row = {
                        column: _normalize_scalar(value)
                        for column, value in zip(columns, raw_row, strict=True)
                    }
                    serialized_bytes += len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
                    if serialized_bytes > 5 * 1024 * 1024:
                        raise SqlResultLimitExceeded(
                            "byte_limit",
                            "Query returned more than 5MB",
                        )
                    rows.append(row)
            return QueryResult(columns=columns, rows=rows, explain=summary)
        except psycopg.Error as error:
            raise SqlExecutionError(
                "postgres_error",
                "PostgreSQL rejected the query",
                sqlstate=error.sqlstate,
            ) from error
        finally:
            try:
                await connection.rollback()
            finally:
                await connection.close()
