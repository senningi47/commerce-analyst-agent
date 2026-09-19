from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from commerce_agent.model._postgres_turn_store import PostgresProviderTurnStore
from commerce_agent.model._turn_store import AttemptRef
from commerce_agent.model.contracts import ProviderTurnRef
from commerce_agent.model.errors import ModelStateError, ModelTransportError
from tests.contracts.provider_turn_store import (
    NOW,
    assert_provider_turn_store_contract,
    private_turn,
)


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current

    def advance(self, **delta: int) -> None:
        self.current += timedelta(**delta)


class FakeCursor:
    def __init__(self, rows: Sequence[tuple[object, ...]] = ()) -> None:
        self._rows = list(rows)

    async def fetchone(self) -> tuple[object, ...] | None:
        return self._rows.pop(0) if self._rows else None


class FakeAsyncConnection:
    def __init__(self, rows: Sequence[tuple[object, ...]] = ()) -> None:
        self.rows = rows
        self.statements: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    async def execute(self, query: str, params: object = None) -> FakeCursor:
        self.statements.append((query, params))
        return FakeCursor(self.rows)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def close(self) -> None:
        self.closes += 1


class StatefulFakeBackend:
    def __init__(self) -> None:
        self.rows: dict[UUID, list[object]] = {}
        self.connections: list[StatefulFakeConnection] = []

    async def connect(self, *_args: Any, **_kwargs: Any) -> StatefulFakeConnection:
        connection = StatefulFakeConnection(self)
        self.connections.append(connection)
        return connection


class StatefulFakeConnection(FakeAsyncConnection):
    def __init__(self, backend: StatefulFakeBackend) -> None:
        super().__init__()
        self.backend = backend

    async def execute(self, query: str, params: object = None) -> FakeCursor:
        self.statements.append((query, params))
        if query.startswith("INSERT INTO"):
            assert isinstance(params, tuple)
            if any(
                row[1:4] == [params[1], params[2], params[3]]
                for row in self.backend.rows.values()
            ):
                raise psycopg.errors.UniqueViolation("duplicate synthetic sequence")
            payload = params[6]
            assert isinstance(payload, Jsonb)
            self.backend.rows[params[0]] = [
                *params[:6],
                payload.obj,
                *params[7:],
                None,
            ]
            return FakeCursor()
        if query.startswith("SELECT"):
            assert isinstance(params, tuple)
            row = self.backend.rows.get(params[0])
            return FakeCursor((tuple(row),) if row is not None else ())
        if query.startswith("UPDATE") and "SET last_used_at" in query:
            assert isinstance(params, tuple)
            row = self.backend.rows[params[1]]
            if row[1] == params[2] and row[2] == params[3] and row[13] is None:
                row[11] = params[0]
            return FakeCursor()
        if query.startswith("UPDATE") and "SET terminal_at" in query:
            assert isinstance(params, tuple)
            for row in self.backend.rows.values():
                if row[1] == params[1] and row[2] == params[2] and row[13] is None:
                    row[13] = params[0]
                    row[6] = None
            return FakeCursor()
        return FakeCursor()


@pytest.mark.asyncio
async def test_save_uses_guarded_parameterized_transaction_and_returns_opaque_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeAsyncConnection()
    connect_calls: list[tuple[str, dict[str, Any]]] = []

    async def connect(dsn: str, **kwargs: Any) -> FakeAsyncConnection:
        connect_calls.append((dsn, kwargs))
        return connection

    monkeypatch.setattr(
        "commerce_agent.model._postgres_turn_store.psycopg.AsyncConnection.connect",
        connect,
    )
    store = PostgresProviderTurnStore(
        SecretStr("postgresql://model_state_writer:synthetic@db/name"),
        clock=MutableClock(NOW),
    )
    turn = private_turn(tool_call_ids=("call_1",))

    ref = await store.save(turn)

    assert connect_calls == [
        (
            "postgresql://model_state_writer:synthetic@db/name",
            {"application_name": "commerce_model_state", "connect_timeout": 3},
        )
    ]
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closes == 1
    statements = [statement for statement, _ in connection.statements]
    assert statements[:4] == [
        "BEGIN",
        "SET LOCAL statement_timeout = '5s'",
        "SET LOCAL lock_timeout = '1s'",
        "SET LOCAL idle_in_transaction_session_timeout = '10s'",
    ]
    insert = next(statement for statement in statements if statement.startswith("INSERT INTO"))
    assert insert.count("%s") == 13
    assert "PRIVATE_CONTENT_SENTINEL" not in insert
    assert "PRIVATE_REASONING_SENTINEL" not in insert
    assert ref.scope_digest == turn.scope_digest
    assert ref.attempt_id == turn.attempt_id
    assert ref.sequence == turn.sequence
    assert ref.payload_sha256 == turn.payload_sha256
    assert "PRIVATE_CONTENT_SENTINEL" not in ref.model_dump_json()


@pytest.mark.asyncio
async def test_delete_attempt_nulls_payload_and_marks_only_exact_scope_attempt_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeAsyncConnection()

    async def connect(*_args: Any, **_kwargs: Any) -> FakeAsyncConnection:
        return connection

    monkeypatch.setattr(
        "commerce_agent.model._postgres_turn_store.psycopg.AsyncConnection.connect",
        connect,
    )
    clock = MutableClock(NOW)
    store = PostgresProviderTurnStore(SecretStr("postgresql://writer:test@db/name"), clock=clock)
    attempt = AttemptRef(scope_digest="a" * 64, attempt_id=private_turn().attempt_id)

    await store.delete_attempt(attempt)

    update, params = next(
        (statement, params)
        for statement, params in connection.statements
        if statement.startswith("UPDATE")
    )
    assert "SET terminal_at = %s, payload = NULL" in update
    assert "WHERE scope_digest = %s AND attempt_id = %s" in update
    assert params == (clock.current, attempt.scope_digest, attempt.attempt_id)
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closes == 1


@pytest.mark.asyncio
async def test_postgres_store_satisfies_shared_provider_turn_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = StatefulFakeBackend()
    monkeypatch.setattr(
        "commerce_agent.model._postgres_turn_store.psycopg.AsyncConnection.connect",
        backend.connect,
    )
    clock = MutableClock(NOW)

    await assert_provider_turn_store_contract(
        PostgresProviderTurnStore(SecretStr("postgresql://writer:test@db/name"), clock=clock),
        clock,
    )

    assert backend.connections
    assert all(connection.closes == 1 for connection in backend.connections)


@pytest.mark.asyncio
async def test_save_rejects_digest_tamper_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected = False

    async def connect(*_args: Any, **_kwargs: Any) -> FakeAsyncConnection:
        nonlocal connected
        connected = True
        return FakeAsyncConnection()

    monkeypatch.setattr(
        "commerce_agent.model._postgres_turn_store.psycopg.AsyncConnection.connect",
        connect,
    )
    store = PostgresProviderTurnStore(
        SecretStr("postgresql://writer:test@db/name"),
        clock=MutableClock(NOW),
    )

    with pytest.raises(ModelStateError) as caught:
        await store.save(private_turn().model_copy(update={"payload_sha256": "f" * 64}))

    assert caught.value.reason_code == "state_digest_mismatch"
    assert connected is False


@pytest.mark.asyncio
async def test_duplicate_sequence_is_sanitized_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = StatefulFakeBackend()
    monkeypatch.setattr(
        "commerce_agent.model._postgres_turn_store.psycopg.AsyncConnection.connect",
        backend.connect,
    )
    store = PostgresProviderTurnStore(
        SecretStr("postgresql://writer:PRIVATE_PASSWORD@db/name"),
        clock=MutableClock(NOW),
    )
    turn = private_turn()
    await store.save(turn)

    with pytest.raises(ModelStateError) as caught:
        await store.save(turn)

    assert caught.value.reason_code == "state_sequence_duplicate"
    assert "PRIVATE_PASSWORD" not in str(caught.value)
    assert backend.connections[-1].rollbacks == 1
    assert backend.connections[-1].closes == 1


@pytest.mark.asyncio
async def test_connection_failure_is_a_sanitized_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def connect(*_args: Any, **_kwargs: Any) -> FakeAsyncConnection:
        raise psycopg.OperationalError("postgresql://writer:PRIVATE_PASSWORD@db/name")

    monkeypatch.setattr(
        "commerce_agent.model._postgres_turn_store.psycopg.AsyncConnection.connect",
        connect,
    )
    store = PostgresProviderTurnStore(
        SecretStr("postgresql://writer:PRIVATE_PASSWORD@db/name"),
        clock=MutableClock(NOW),
    )

    with pytest.raises(ModelTransportError) as caught:
        await store.save(private_turn())

    assert caught.value.reason_code == "state_connection_failed"
    assert caught.value.retryable is True
    assert "PRIVATE_PASSWORD" not in str(caught.value)


@pytest.mark.parametrize(
    "delta",
    [timedelta(hours=24, seconds=1), timedelta(days=7)],
)
@pytest.mark.asyncio
async def test_resolve_rejects_idle_and_absolute_expiry_without_renewal(
    monkeypatch: pytest.MonkeyPatch,
    delta: timedelta,
) -> None:
    backend = StatefulFakeBackend()
    monkeypatch.setattr(
        "commerce_agent.model._postgres_turn_store.psycopg.AsyncConnection.connect",
        backend.connect,
    )
    clock = MutableClock(NOW)
    store = PostgresProviderTurnStore(
        SecretStr("postgresql://writer:test@db/name"),
        clock=clock,
    )
    ref = await store.save(private_turn())
    clock.current = NOW + delta

    with pytest.raises(ModelStateError) as caught:
        await store.resolve(ref)

    assert caught.value.reason_code == "state_expired"
    assert backend.connections[-1].rollbacks == 1
    assert not any(
        statement.startswith("UPDATE")
        for statement, _ in backend.connections[-1].statements
    )


@pytest.mark.asyncio
async def test_resolve_rejects_tampered_private_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = StatefulFakeBackend()
    monkeypatch.setattr(
        "commerce_agent.model._postgres_turn_store.psycopg.AsyncConnection.connect",
        backend.connect,
    )
    store = PostgresProviderTurnStore(
        SecretStr("postgresql://writer:test@db/name"),
        clock=MutableClock(NOW),
    )
    ref = await store.save(private_turn())
    payload = backend.rows[ref.turn_id][6]
    assert isinstance(payload, dict)
    payload["reasoning_content"] = "TAMPERED_PRIVATE_PAYLOAD"

    with pytest.raises(ModelStateError) as caught:
        await store.resolve(ref)

    assert caught.value.reason_code == "state_digest_mismatch"
    assert "TAMPERED_PRIVATE_PAYLOAD" not in str(caught.value)
    assert backend.connections[-1].rollbacks == 1


@pytest.mark.asyncio
async def test_resolve_locks_exact_row_validates_binding_and_renews_only_last_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turn = private_turn(tool_call_ids=("call_1",))
    turn_id = UUID("00000000-0000-0000-0000-000000000210")
    ref = ProviderTurnRef(
        turn_id=turn_id,
        scope_digest=turn.scope_digest,
        attempt_id=turn.attempt_id,
        sequence=turn.sequence,
        payload_sha256=turn.payload_sha256,
        expected_tool_call_ids=turn.expected_tool_call_ids,
        token_weight=turn.token_weight,
        expires_at=turn.absolute_expires_at,
    )
    row = (
        turn_id,
        turn.scope_digest,
        turn.attempt_id,
        turn.sequence,
        turn.provider,
        turn.model,
        {
            "content": turn.content,
            "reasoning_content": turn.reasoning_content,
            "tool_calls": __import__("json").loads(turn.tool_calls_json),
        },
        turn.payload_sha256,
        list(turn.expected_tool_call_ids),
        turn.token_weight,
        turn.created_at,
        turn.last_used_at,
        turn.absolute_expires_at,
        None,
    )
    connection = FakeAsyncConnection(rows=(row,))

    async def connect(*_args: Any, **_kwargs: Any) -> FakeAsyncConnection:
        return connection

    monkeypatch.setattr(
        "commerce_agent.model._postgres_turn_store.psycopg.AsyncConnection.connect",
        connect,
    )
    clock = MutableClock(NOW + timedelta(hours=1))
    store = PostgresProviderTurnStore(SecretStr("postgresql://writer:test@db/name"), clock=clock)

    resolved = await store.resolve(ref)

    assert resolved.last_used_at == clock.current
    statements = [statement for statement, _ in connection.statements]
    select = next(statement for statement in statements if statement.startswith("SELECT"))
    assert "WHERE turn_id = %s FOR UPDATE" in select
    update = next(statement for statement in statements if statement.startswith("UPDATE"))
    assert "SET last_used_at = %s" in update
    assert "payload =" not in update
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closes == 1
