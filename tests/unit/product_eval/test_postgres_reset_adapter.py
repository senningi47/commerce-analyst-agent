
import pytest
from pydantic import SecretStr

from commerce_agent.operations.errors import (
    OperationContractError,
    OperationInfrastructureError,
)
from commerce_agent.product_eval._reset import PostgresScenarioReset


def reset_dsn(
    *,
    role: str = "product_scenario_reset",
    application_name: str = "commerce_product_scenario_reset",
    database: str = "commerce_analyst",
    host: str = "127.0.0.1",
    port: str = "5432",
    search_path: str = "trusted_schema,ops,pg_catalog",
) -> SecretStr:
    return SecretStr(
        f"postgresql://{role}:test-only@{host}:{port}/{database}"
        f"?application_name={application_name}"
        f"&options=-csearch_path%3D{search_path}"
    )


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object | None]] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    async def execute(self, query: str, params: object | None = None) -> None:
        self.calls.append((query, params))

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def close(self) -> None:
        self.closed = True


class FakeConnect:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def __call__(self, *args: object, **kwargs: object) -> FakeConnection:
        self.calls.append((args, kwargs))
        return self.connection


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("role", "agent_reader"),
        ("application_name", "commerce_query_engine"),
        ("database", "other_database"),
        ("host", "localhost"),
        ("port", "5433"),
        ("search_path", "ops,trusted_schema,pg_catalog"),
    ],
)
@pytest.mark.asyncio
async def test_reset_adapter_rejects_wrong_database_identity_before_connect(
    field: str,
    wrong_value: str,
) -> None:
    connect = FakeConnect(FakeConnection())
    reset = PostgresScenarioReset(
        reset_dsn(**{field: wrong_value}),
        connect=connect,
    )

    with pytest.raises(OperationInfrastructureError) as caught:
        await reset.open()

    assert caught.value.reason_code == "scenario_reset_database_identity_invalid"
    assert caught.value.retryable is False
    assert connect.calls == []


@pytest.mark.asyncio
async def test_reset_adapter_calls_only_fixed_scenario_function() -> None:
    connection = FakeConnection()
    connect = FakeConnect(connection)
    reset = PostgresScenarioReset(reset_dsn(), connect=connect)

    await reset.reset("seller-risk-postgres-v1", "scenario-reset-v1")

    assert connect.calls[0][1] == {"connect_timeout": 3}
    assert [query for query, _params in connection.calls[:4]] == [
        "BEGIN",
        "SET LOCAL statement_timeout = '5s'",
        "SET LOCAL lock_timeout = '1s'",
        "SET LOCAL idle_in_transaction_session_timeout = '10s'",
    ]
    query, params = connection.calls[4]
    assert query == "SELECT trusted_schema.reset_product_scenario(%s)"
    assert params == ("seller-risk-postgres-v1",)
    assert connection.committed is True
    assert connection.closed is True


@pytest.mark.parametrize(
    ("scenario_id", "manifest_revision"),
    [
        ("invalid scenario", "scenario-reset-v1"),
        ("seller-risk-postgres-v1", "scenario-reset-v2"),
    ],
)
@pytest.mark.asyncio
async def test_reset_adapter_rejects_unreviewed_scope_before_connect(
    scenario_id: str,
    manifest_revision: str,
) -> None:
    connect = FakeConnect(FakeConnection())
    reset = PostgresScenarioReset(reset_dsn(), connect=connect)

    with pytest.raises(OperationContractError) as caught:
        await reset.reset(scenario_id, manifest_revision)

    assert caught.value.reason_code == "scenario_reset_scope_invalid"
    assert connect.calls == []
