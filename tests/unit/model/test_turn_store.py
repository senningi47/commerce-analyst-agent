from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from commerce_agent.model._turn_store import (
    AttemptRef,
    InMemoryProviderTurnStore,
    PrivateProviderTurn,
)
from commerce_agent.model.contracts import ProviderTurnRef
from commerce_agent.model.errors import ModelStateError
from tests.contracts.provider_turn_store import assert_provider_turn_store_contract

ATTEMPT_A = UUID("00000000-0000-0000-0000-000000000201")
ATTEMPT_B = UUID("00000000-0000-0000-0000-000000000202")
NOW = datetime(2026, 9, 4, 3, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current

    def advance(self, **delta: int) -> None:
        self.current += timedelta(**delta)


def private_turn(*, attempt_id: UUID = ATTEMPT_A, sequence: int = 0) -> PrivateProviderTurn:
    return PrivateProviderTurn(
        scope_digest="a" * 64,
        attempt_id=attempt_id,
        sequence=sequence,
        provider="deepseek",
        model="deepseek-v4-flash",
        content="public",
        reasoning_content="PRIVATE_REASONING_SENTINEL",
        tool_calls_json="[]",
        payload_sha256="291f33f3c3b89a929ec8f4ac1ab7ca01d123624aca10c818049768882fc4abcf",
        expected_tool_call_ids=(),
        token_weight=12,
        created_at=NOW,
        last_used_at=NOW,
        absolute_expires_at=NOW + timedelta(days=7),
    )


@pytest.mark.asyncio
async def test_memory_store_rejects_cross_attempt_and_expired_refs() -> None:
    clock = MutableClock(NOW)
    store = InMemoryProviderTurnStore(clock=clock)
    turn = private_turn(attempt_id=ATTEMPT_A, sequence=0)
    ref = await store.save(turn)

    assert (await store.resolve(ref)).payload_sha256 == ref.payload_sha256
    cross_attempt = ProviderTurnRef.model_validate(ref.model_dump() | {"attempt_id": ATTEMPT_B})
    with pytest.raises(ModelStateError, match="attempt"):
        await store.resolve(cross_attempt)

    clock.advance(days=8)
    with pytest.raises(ModelStateError) as caught:
        await store.resolve(ref)
    assert caught.value.reason_code == "state_expired"


@pytest.mark.asyncio
async def test_delete_attempt_removes_every_private_turn() -> None:
    store = InMemoryProviderTurnStore(clock=MutableClock(NOW))
    refs = [await store.save(private_turn(sequence=n)) for n in range(2)]
    await store.delete_attempt(AttemptRef(scope_digest="a" * 64, attempt_id=ATTEMPT_A))
    for ref in refs:
        with pytest.raises(ModelStateError, match="not found"):
            await store.resolve(ref)


@pytest.mark.asyncio
async def test_memory_store_satisfies_shared_contract() -> None:
    clock = MutableClock(NOW)
    await assert_provider_turn_store_contract(
        InMemoryProviderTurnStore(clock=clock),
        clock,
    )


@pytest.mark.asyncio
async def test_save_recomputes_digest_and_rejects_duplicate_sequence() -> None:
    store = InMemoryProviderTurnStore(clock=MutableClock(NOW))
    turn = private_turn()
    with pytest.raises(ModelStateError) as caught:
        await store.save(turn.model_copy(update={"payload_sha256": "f" * 64}))
    assert caught.value.reason_code == "state_digest_mismatch"

    await store.save(turn)
    with pytest.raises(ModelStateError) as caught:
        await store.save(turn)
    assert caught.value.reason_code == "state_sequence_duplicate"


def test_private_turn_expected_ids_must_match_canonical_tool_calls() -> None:
    with pytest.raises(ValueError, match="expected tool call ids"):
        PrivateProviderTurn.model_validate(
            private_turn().model_dump()
            | {
                "tool_calls_json": '[{"id":"call_1"}]',
                "expected_tool_call_ids": ("call_2",),
            }
        )


@pytest.mark.asyncio
async def test_idle_expiry_is_sliding_and_absolute_expiry_is_fixed() -> None:
    idle_clock = MutableClock(NOW)
    idle_store = InMemoryProviderTurnStore(clock=idle_clock)
    idle_ref = await idle_store.save(private_turn())
    idle_clock.advance(hours=24, seconds=1)
    with pytest.raises(ModelStateError) as caught:
        await idle_store.resolve(idle_ref)
    assert caught.value.reason_code == "state_expired"

    absolute_clock = MutableClock(NOW)
    absolute_store = InMemoryProviderTurnStore(clock=absolute_clock)
    absolute_ref = await absolute_store.save(private_turn())
    for _ in range(6):
        absolute_clock.advance(hours=23)
        await absolute_store.resolve(absolute_ref)
    absolute_clock.current = NOW + timedelta(days=7)
    with pytest.raises(ModelStateError) as caught:
        await absolute_store.resolve(absolute_ref)
    assert caught.value.reason_code == "state_expired"
