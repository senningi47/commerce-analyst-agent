import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from pydantic import SecretStr

from commerce_agent.model._postgres_turn_store import PostgresProviderTurnStore
from commerce_agent.model._turn_store import AttemptRef
from commerce_agent.model.errors import ModelStateError
from tests.contracts.provider_turn_store import (
    ATTEMPT_A,
    ATTEMPT_B,
    NOW,
    assert_provider_turn_store_contract,
    private_turn,
)

pytestmark = pytest.mark.postgres


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current

    def advance(self, **delta: int) -> None:
        self.current += timedelta(**delta)


async def _delete_contract_rows() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        await connection.execute(
            "DELETE FROM model_state.provider_turn "
            "WHERE scope_digest = %s AND attempt_id = ANY(%s)",
            ("a" * 64, [ATTEMPT_A, ATTEMPT_B]),
        )
        await connection.commit()
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_real_postgres_store_satisfies_shared_contract() -> None:
    await _delete_contract_rows()
    clock = MutableClock(NOW)
    store = PostgresProviderTurnStore(
        SecretStr(os.environ["PRODUCT_MODEL_STATE_DATABASE_DSN"]),
        clock=clock,
    )
    try:
        await assert_provider_turn_store_contract(store, clock)
    finally:
        await _delete_contract_rows()


@pytest.mark.asyncio
async def test_real_store_enforces_uniqueness_and_terminal_payload_deletion() -> None:
    clock = MutableClock(datetime(2026, 9, 6, 0, 0, tzinfo=UTC))
    store = PostgresProviderTurnStore(
        SecretStr(os.environ["PRODUCT_MODEL_STATE_DATABASE_DSN"]),
        clock=clock,
    )
    attempt_id = uuid4()
    turn = private_turn(attempt_id=attempt_id).model_copy(
        update={
            "created_at": clock.current,
            "last_used_at": clock.current,
            "absolute_expires_at": clock.current + timedelta(days=7),
        }
    )
    ref = await store.save(turn)
    try:
        with pytest.raises(ModelStateError) as caught:
            await store.save(turn)
        assert caught.value.reason_code == "state_sequence_duplicate"

        await store.delete_attempt(
            AttemptRef(scope_digest=turn.scope_digest, attempt_id=attempt_id)
        )
        with pytest.raises(ModelStateError) as deleted:
            await store.resolve(ref)
        assert deleted.value.reason_code == "state_not_found"

        connection = await psycopg.AsyncConnection.connect(
            os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
        )
        try:
            cursor = await connection.execute(
                "SELECT payload, terminal_at FROM model_state.provider_turn "
                "WHERE turn_id = %s",
                (ref.turn_id,),
            )
            payload, terminal_at = await cursor.fetchone()
            assert payload is None
            assert terminal_at == clock.current
        finally:
            await connection.close()
    finally:
        connection = await psycopg.AsyncConnection.connect(
            os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
        )
        try:
            await connection.execute(
                "DELETE FROM model_state.provider_turn WHERE attempt_id = %s",
                (attempt_id,),
            )
            await connection.commit()
        finally:
            await connection.close()


@pytest.mark.asyncio
async def test_real_store_rejects_idle_and_absolute_expiry() -> None:
    for delta in (timedelta(hours=24, seconds=1), timedelta(days=7)):
        attempt_id = uuid4()
        clock = MutableClock(NOW)
        store = PostgresProviderTurnStore(
            SecretStr(os.environ["PRODUCT_MODEL_STATE_DATABASE_DSN"]),
            clock=clock,
        )
        turn = private_turn(attempt_id=attempt_id)
        ref = await store.save(turn)
        clock.current = NOW + delta
        try:
            with pytest.raises(ModelStateError) as caught:
                await store.resolve(ref)
            assert caught.value.reason_code == "state_expired"
        finally:
            connection = await psycopg.AsyncConnection.connect(
                os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
            )
            try:
                await connection.execute(
                    "DELETE FROM model_state.provider_turn WHERE attempt_id = %s",
                    (attempt_id,),
                )
                await connection.commit()
            finally:
                await connection.close()
