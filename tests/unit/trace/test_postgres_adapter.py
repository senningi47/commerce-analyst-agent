from datetime import UTC, datetime
from uuid import UUID

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
from commerce_agent.trace.errors import TraceContractError


def trace_dsn(
    *,
    role: str = "trace_writer",
    application_name: str = "commerce_product_trace",
    database: str = "commerce_analyst",
    host: str = "127.0.0.1",
    port: str = "5432",
    search_path: str = "app,pg_catalog",
) -> SecretStr:
    return SecretStr(
        f"postgresql://{role}:test-only@{host}:{port}/{database}"
        f"?application_name={application_name}"
        f"&options=-csearch_path%3D{search_path}"
    )


class FakeCursor:
    async def fetchone(self) -> tuple[object, ...] | None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object | None]] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    async def execute(self, query: str, params: object | None = None) -> FakeCursor:
        self.calls.append((query, params))
        return FakeCursor()

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


def trace_event() -> ScopedTraceEvent:
    config_hash = "a" * 64
    return ScopedTraceEvent(
        run_scope=RunScope(
            run_id=UUID(int=1),
            track="retail",
            mode="retail",
            subject_id="seller-risk-postgres-v1",
            experiment_id="day4-task15",
            config_hash=config_hash,
        ),
        attempt_id=UUID(int=2),
        phase="proposal",
        sequence=0,
        occurred_at=datetime(2026, 9, 7, 7, 0, tzinfo=UTC),
        node="create_proposal",
        event_type=TraceEventType.PROPOSAL_CREATED,
        status=TraceStatus.SUCCEEDED,
        decision_summary=TraceDecisionSummary(
            code="proposal_persisted",
            text="A reviewed proposal was persisted.",
        ),
        config_hash=config_hash,
        proposal_ref="proposal:00000000-0000-0000-0000-000000000003:1",
    )


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("role", "operation_executor"),
        ("application_name", "commerce_operation_execute"),
        ("database", "other_database"),
        ("host", "localhost"),
        ("port", "5433"),
        ("search_path", "pg_catalog,app"),
    ],
)
@pytest.mark.asyncio
async def test_trace_store_rejects_wrong_identity_before_connect(
    field: str,
    wrong_value: str,
) -> None:
    connection = FakeConnection()
    connect = FakeConnect(connection)
    store = PostgresTraceStore(
        trace_dsn(**{field: wrong_value}),
        scenario_id="seller-risk-postgres-v1",
        connect=connect,
    )

    with pytest.raises(TraceInfrastructureError) as caught:
        await store.open()

    assert caught.value.reason_code == "trace_database_identity_invalid"
    assert caught.value.retryable is False
    assert connect.calls == []


@pytest.mark.asyncio
async def test_trace_store_appends_one_parameterized_event() -> None:
    connection = FakeConnection()
    connect = FakeConnect(connection)
    store = PostgresTraceStore(
        trace_dsn(),
        scenario_id="seller-risk-postgres-v1",
        connect=connect,
    )
    event = trace_event()

    await store.append(event)

    assert connect.calls[0][1] == {"connect_timeout": 3}
    assert [query for query, _params in connection.calls[:4]] == [
        "BEGIN",
        "SET LOCAL statement_timeout = '5s'",
        "SET LOCAL lock_timeout = '1s'",
        "SET LOCAL idle_in_transaction_session_timeout = '10s'",
    ]
    insert, params = connection.calls[4]
    assert insert.startswith("INSERT INTO app.product_trace_event (")
    assert insert.count("%s") == 19
    assert params is not None
    assert event.decision_summary is not None
    assert event.decision_summary.text not in insert
    assert connection.committed is True
    assert connection.closed is True


@pytest.mark.asyncio
async def test_trace_store_rejects_bird_scope_before_connect() -> None:
    connection = FakeConnection()
    connect = FakeConnect(connection)
    store = PostgresTraceStore(
        trace_dsn(),
        scenario_id="seller-risk-postgres-v1",
        connect=connect,
    )
    event = trace_event().model_copy(
        update={
            "run_scope": RunScope(
                run_id=UUID(int=9),
                track="bird",
                mode="a",
                subject_id="bird-question-001",
                experiment_id="bird-evaluation",
                config_hash="b" * 64,
            )
        }
    )

    with pytest.raises(TraceContractError) as caught:
        await store.append(event)

    assert caught.value.reason_code == "trace_scope_forbidden"
    assert connect.calls == []
