"""Reusable assertions for every provider-private turn store adapter."""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol
from uuid import UUID

import pytest

from commerce_agent.model._turn_store import (
    AttemptRef,
    PrivateProviderTurn,
    ProviderTurnStore,
)
from commerce_agent.model.errors import ModelStateError

ATTEMPT_A = UUID("00000000-0000-0000-0000-000000000201")
ATTEMPT_B = UUID("00000000-0000-0000-0000-000000000202")
NOW = datetime(2026, 9, 4, 3, 0, tzinfo=UTC)


class MutableClock(Protocol):
    current: datetime

    def advance(self, **delta: int) -> None: ...


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def private_turn(
    *,
    attempt_id: UUID = ATTEMPT_A,
    sequence: int = 0,
    tool_call_ids: tuple[str, ...] = (),
) -> PrivateProviderTurn:
    tool_calls = [
        {
            "function": {"arguments": "{}", "name": "lookup_orders"},
            "id": call_id,
            "type": "function",
        }
        for call_id in tool_call_ids
    ]
    private_payload = {
        "content": "PRIVATE_CONTENT_SENTINEL",
        "reasoning_content": "PRIVATE_REASONING_SENTINEL",
        "tool_calls": tool_calls,
    }
    return PrivateProviderTurn(
        scope_digest="a" * 64,
        attempt_id=attempt_id,
        sequence=sequence,
        provider="deepseek",
        model="deepseek-v4-flash",
        content=private_payload["content"],
        reasoning_content=private_payload["reasoning_content"],
        tool_calls_json=canonical_json(tool_calls),
        payload_sha256=sha256(canonical_json(private_payload).encode()).hexdigest(),
        expected_tool_call_ids=tool_call_ids,
        token_weight=12,
        created_at=NOW,
        last_used_at=NOW,
        absolute_expires_at=NOW + timedelta(days=7),
    )


async def assert_provider_turn_store_contract(
    store: ProviderTurnStore,
    clock: MutableClock,
) -> None:
    """Exercise binding, lifecycle, privacy, and deletion invariants."""

    first = private_turn(sequence=0, tool_call_ids=("call_1",))
    second = private_turn(sequence=1)
    first_ref = await store.save(first)
    second_ref = await store.save(second)

    assert first_ref.turn_id != second_ref.turn_id
    assert first_ref.scope_digest == first.scope_digest
    assert first_ref.attempt_id == first.attempt_id
    assert first_ref.sequence == first.sequence
    assert first_ref.payload_sha256 == first.payload_sha256
    assert first_ref.expected_tool_call_ids == first.expected_tool_call_ids
    assert first_ref.expires_at == first.absolute_expires_at
    for sentinel in ("PRIVATE_CONTENT_SENTINEL", "PRIVATE_REASONING_SENTINEL"):
        assert sentinel not in repr(first)
        assert sentinel not in first_ref.model_dump_json()

    clock.advance(hours=1)
    renewed = await store.resolve(first_ref)
    untouched = await store.resolve(second_ref)
    assert renewed.last_used_at == clock.current
    assert untouched.created_at == second.created_at

    for field, value in (
        ("scope_digest", "b" * 64),
        ("attempt_id", ATTEMPT_B),
        ("sequence", 99),
        ("payload_sha256", "c" * 64),
        ("expected_tool_call_ids", ("other_call",)),
        ("token_weight", 99),
        ("expires_at", first_ref.expires_at + timedelta(seconds=1)),
    ):
        with pytest.raises(ModelStateError) as caught:
            await store.resolve(first_ref.model_copy(update={field: value}))
        assert caught.value.reason_code == "state_binding_mismatch"

    other_ref = await store.save(private_turn(attempt_id=ATTEMPT_B))
    await store.delete_attempt(AttemptRef(scope_digest=first.scope_digest, attempt_id=ATTEMPT_A))
    for deleted_ref in (first_ref, second_ref):
        with pytest.raises(ModelStateError) as caught:
            await store.resolve(deleted_ref)
        assert caught.value.reason_code == "state_not_found"
    assert (await store.resolve(other_ref)).attempt_id == ATTEMPT_B
