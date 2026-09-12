from datetime import UTC, datetime
from uuid import UUID

import pytest

from commerce_agent.model.contracts import RunScope
from commerce_agent.trace import (
    InMemoryTraceStore,
    ScopedTraceEvent,
    TraceEventType,
    TraceModule,
    TraceStatus,
)
from commerce_agent.trace.errors import TraceContractError

TEST_SCOPE = RunScope(
    run_id=UUID(int=100),
    track="retail",
    mode="retail",
    subject_id="day4-scenario",
    experiment_id="day4-product",
    config_hash="a" * 64,
)
TEST_ATTEMPT = UUID(int=101)


def scoped_event(sequence: int, event_type: TraceEventType) -> ScopedTraceEvent:
    return ScopedTraceEvent(
        run_scope=TEST_SCOPE,
        attempt_id=TEST_ATTEMPT,
        phase="investigation",
        sequence=sequence,
        occurred_at=datetime(2026, 9, 6, 4, 0, tzinfo=UTC),
        node="plan",
        event_type=event_type,
        status=TraceStatus.SUCCEEDED,
        config_hash="a" * 64,
    )


@pytest.mark.asyncio
async def test_trace_appends_monotonic_sanitized_events() -> None:
    store = InMemoryTraceStore()
    trace = TraceModule(store=store)
    first = scoped_event(0, TraceEventType.INVESTIGATION_PLAN_ACCEPTED)
    second = scoped_event(1, TraceEventType.PROPOSAL_CREATED)

    await trace.append(first)
    await trace.append(second)

    assert await store.events(first.run_scope, first.attempt_id) == (first, second)


@pytest.mark.asyncio
async def test_duplicate_or_out_of_order_sequence_fails_without_append() -> None:
    store = InMemoryTraceStore()
    trace = TraceModule(store=store)
    await trace.append(scoped_event(0, TraceEventType.INVESTIGATION_PLAN_ACCEPTED))

    with pytest.raises(TraceContractError) as caught:
        await trace.append(scoped_event(0, TraceEventType.PROPOSAL_CREATED))

    assert caught.value.reason_code == "trace_sequence_invalid"
    assert len(await store.events(TEST_SCOPE, TEST_ATTEMPT)) == 1
