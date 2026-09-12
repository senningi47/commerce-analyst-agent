"""Small public append seam for sanitized Trace events."""

from typing import Protocol

from commerce_agent.trace.contracts import ScopedTraceEvent
from commerce_agent.trace.errors import TraceContractError


class TracePort(Protocol):
    async def append(self, event: ScopedTraceEvent) -> None: ...


_SENSITIVE_MARKERS = (
    "://",
    "password",
    "secret",
    "sk-",
    "raw_seller_id",
    "reasoning_content",
    "approval_nonce",
    "signature",
)


def validate_trace_event(event: ScopedTraceEvent) -> None:
    summary = event.decision_summary
    if summary is None:
        return
    normalized = summary.text.casefold()
    if any(marker in normalized for marker in _SENSITIVE_MARKERS):
        raise TraceContractError("trace_sensitive_content")


class TraceModule:
    def __init__(self, *, store: TracePort) -> None:
        self._store = store

    async def append(self, event: ScopedTraceEvent) -> None:
        validate_trace_event(event)
        await self._store.append(event)
