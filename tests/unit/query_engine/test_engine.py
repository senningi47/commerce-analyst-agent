import pytest

from commerce_agent.query_engine._ast_policy import AstPolicy, ValidatedQuery
from commerce_agent.query_engine.contracts import QueryRequest, QueryResult
from commerce_agent.query_engine.engine import QueryEngine
from commerce_agent.query_engine.errors import SqlPolicyViolation


class RejectingPolicy:
    def validate(self, sql: str) -> ValidatedQuery:
        del sql
        raise SqlPolicyViolation("root_not_select", "Only SELECT is allowed")


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[ValidatedQuery] = []

    async def execute(self, query: ValidatedQuery) -> QueryResult:
        self.calls.append(query)
        return QueryResult(columns=[], rows=[])


@pytest.mark.asyncio
async def test_policy_rejection_does_not_call_postgres() -> None:
    executor = RecordingExecutor()
    engine = QueryEngine(policy=RejectingPolicy(), executor=executor)

    with pytest.raises(SqlPolicyViolation):
        await engine.execute(QueryRequest(sql="UPDATE retail.orders SET order_status='x'"))

    assert executor.calls == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; SELECT 2",
        "UPDATE retail.orders SET order_status = 'x'",
        "WITH changed AS (DELETE FROM retail.orders RETURNING *) SELECT * FROM changed",
        "WITH RECURSIVE n AS (SELECT 1 UNION ALL SELECT 1) SELECT * FROM n",
        "SELECT order_id INTO copied_orders FROM retail.orders",
        "SELECT order_id FROM retail.orders FOR UPDATE",
        ("SELECT o.order_id FROM retail.orders AS o CROSS JOIN retail.customers AS c LIMIT 10"),
        "SELECT * FROM pg_catalog.pg_roles LIMIT 10",
        "SELECT nextval('retail.permission_probe_sequence')",
        "SELECT pg_sleep(1) FROM retail.orders LIMIT 10",
    ],
)
@pytest.mark.asyncio
async def test_ast_attacks_never_reach_executor(sql: str) -> None:
    executor = RecordingExecutor()
    engine = QueryEngine(policy=AstPolicy(), executor=executor)

    with pytest.raises(SqlPolicyViolation):
        await engine.execute(QueryRequest(sql=sql))

    assert executor.calls == []
