"""Deterministic append-only in-memory Trace adapter."""

import asyncio
from collections import defaultdict
from uuid import UUID

from commerce_agent.model.contracts import RunScope
from commerce_agent.trace.contracts import ScopedTraceEvent
from commerce_agent.trace.errors import TraceContractError


class InMemoryTraceStore:
    def __init__(self) -> None:
        self._events: dict[tuple[RunScope, UUID], list[ScopedTraceEvent]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def append(self, event: ScopedTraceEvent) -> None:
        key = (event.run_scope, event.attempt_id)
        async with self._lock:
            existing = self._events[key]
            if event.sequence != len(existing):
                raise TraceContractError("trace_sequence_invalid")
            existing.append(event)

    async def events(
        self, run_scope: RunScope, attempt_id: UUID
    ) -> tuple[ScopedTraceEvent, ...]:
        async with self._lock:
            return tuple(self._events[(run_scope, attempt_id)])
