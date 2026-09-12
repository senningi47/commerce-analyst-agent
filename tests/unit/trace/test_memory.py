from uuid import UUID

import pytest

from commerce_agent.trace import InMemoryTraceStore
from commerce_agent.trace.contracts import TraceEventType
from tests.unit.trace.test_module import TEST_ATTEMPT, TEST_SCOPE, scoped_event


@pytest.mark.asyncio
async def test_trace_sequences_are_isolated_by_scope_and_attempt() -> None:
    store = InMemoryTraceStore()
    await store.append(scoped_event(0, TraceEventType.PROPOSAL_CREATED))

    assert await store.events(TEST_SCOPE, TEST_ATTEMPT) == (
        scoped_event(0, TraceEventType.PROPOSAL_CREATED),
    )
    assert await store.events(TEST_SCOPE, UUID(int=999)) == ()


def test_trace_store_exposes_no_mutation_api() -> None:
    store = InMemoryTraceStore()

    assert not hasattr(store, "update")
    assert not hasattr(store, "delete")
