import os

import psycopg
import pytest
from pydantic import SecretStr

from commerce_agent.query_engine._ast_policy import AstPolicy, ValidatedQuery
from commerce_agent.query_engine._postgres import PostgresExecutor
from commerce_agent.query_engine.contracts import QueryRequest, QueryResult
from commerce_agent.query_engine.engine import QueryEngine
from commerce_agent.query_engine.errors import SqlPolicyViolation

pytestmark = pytest.mark.postgres


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[ValidatedQuery] = []

    async def execute(self, query: ValidatedQuery) -> QueryResult:
        self.calls.append(query)
        return QueryResult(columns=[], rows=[])


@pytest.fixture
async def permission_probe_sequence() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        await connection.execute(
            "DROP SEQUENCE IF EXISTS retail.permission_probe_sequence"
        )
        await connection.execute("CREATE SEQUENCE retail.permission_probe_sequence")
        await connection.commit()
        yield
    finally:
        try:
            await connection.rollback()
            await connection.execute(
                "DROP SEQUENCE IF EXISTS retail.permission_probe_sequence"
            )
            await connection.commit()
        finally:
            await connection.close()


def _assert_dsn_is_sanitized(error: BaseException) -> None:
    reader_dsn = os.environ["PRODUCT_DATABASE_DSN"]
    assert reader_dsn not in str(error)
    assert reader_dsn not in repr(error)


@pytest.mark.asyncio
async def test_reader_cannot_update_after_disabling_role_default() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_DATABASE_DSN"],
        autocommit=True,
    )
    try:
        await connection.execute("SET default_transaction_read_only = off")
        with pytest.raises(psycopg.Error) as captured:
            await connection.execute(
                "UPDATE retail.orders SET order_status = 'tampered' WHERE false"
            )

        assert captured.value.sqlstate == "42501"
        _assert_dsn_is_sanitized(captured.value)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_reader_cannot_create_temporary_tables() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_DATABASE_DSN"]
    )
    try:
        with pytest.raises(psycopg.Error) as captured:
            await connection.execute("CREATE TEMP TABLE escaped(value integer)")

        assert captured.value.sqlstate in {"25006", "42501"}
        _assert_dsn_is_sanitized(captured.value)
    finally:
        await connection.rollback()
        await connection.close()


@pytest.mark.asyncio
async def test_reader_cannot_use_ungranted_sequences(
    permission_probe_sequence: None,
) -> None:
    del permission_probe_sequence
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_DATABASE_DSN"]
    )
    try:
        with pytest.raises(psycopg.Error) as captured:
            await connection.execute(
                "SELECT nextval('retail.permission_probe_sequence')"
            )

        assert captured.value.sqlstate in {"25006", "42501"}
        _assert_dsn_is_sanitized(captured.value)
    finally:
        await connection.rollback()
        await connection.close()


@pytest.mark.asyncio
async def test_reader_has_no_temp_or_sequence_acl(
    permission_probe_sequence: None,
) -> None:
    del permission_probe_sequence
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT "
            "has_database_privilege('agent_reader', 'commerce_analyst', 'TEMP'), "
            "has_sequence_privilege("
            "'agent_reader', 'retail.permission_probe_sequence', 'USAGE'"
            ")"
        )
        assert await cursor.fetchone() == (False, False)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_representative_aggregate_runs_through_public_engine() -> None:
    engine = QueryEngine(
        policy=AstPolicy(),
        executor=PostgresExecutor(
            SecretStr(os.environ["PRODUCT_DATABASE_DSN"])
        ),
    )
    request = QueryRequest(
        sql=(
            "SELECT "
            "o.order_status, "
            "COUNT(DISTINCT o.order_id) AS order_count, "
            "ROUND(SUM(i.price), 2) AS item_amount "
            "FROM retail.orders AS o "
            "JOIN retail.order_items AS i ON i.order_id = o.order_id "
            "GROUP BY o.order_status "
            "ORDER BY o.order_status"
        )
    )

    result = await engine.execute(request)

    assert 1 <= result.row_count <= 8
    assert result.explain is not None
    for row in result.rows:
        assert isinstance(row["order_status"], str)
        assert isinstance(row["order_count"], int)
        assert row["order_count"] >= 0
        assert float(row["item_amount"]) >= 0


@pytest.mark.parametrize(
    ("sql", "reason_code"),
    [
        ("SELECT * FROM knowledge.retail_documents LIMIT 1", "schema_denied"),
        ("SELECT * FROM ops_read.business_value_aliases LIMIT 1", "table_denied"),
    ],
)
@pytest.mark.asyncio
async def test_query_engine_never_exposes_knowledge_or_alias_views(
    sql: str,
    reason_code: str,
) -> None:
    executor = RecordingExecutor()
    engine = QueryEngine(policy=AstPolicy(), executor=executor)

    with pytest.raises(SqlPolicyViolation) as caught:
        await engine.execute(QueryRequest(sql=sql))

    assert caught.value.reason_code == reason_code
    assert executor.calls == []
