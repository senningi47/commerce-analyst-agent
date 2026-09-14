"""Stable SSE event surface for the product track (v0.3 §18/§20).

The frontend depends only on these event types - never on graph node or
phase names. The surface is exactly the Product Trace event vocabulary, and
the payload is the trace's whitelisted public summary, so the pushed
sequence is consistent with the closed-loop audit by construction.

Day 6 plan-candidate mapping (trimmed to the real loop events):
approval_required is ``proposal_created`` (an unapproved proposal IS the
approval-pending state), approval_decided is ``decision_recorded``,
execution_receipt is ``execution_completed``, report_ready is
``report_completed``, run_resumed is ``recovery_applied``; a rejected SQL
candidate surfaces through ``sql_repaired`` / status + reason_code rather
than a type of its own.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from commerce_agent.model.contracts import Cost, Usage
from commerce_agent.trace.contracts import (
    ScopedTraceEvent,
    TraceDecisionSummary,
    TraceEventType,
    TraceEvidence,
)

SSE_EVENT_TYPES: frozenset[str] = frozenset(
    item.value for item in TraceEventType
)

_PAYLOAD_MODEL = Annotated[
    Usage | None,
    Field(discriminator="status"),
]
_PAYLOAD_COST = Annotated[
    Cost | None,
    Field(discriminator="status"),
]


class SseRunEvent(BaseModel):
    """One whitelisted public run event; the SSE payload schema.

    ``cursor`` is the run-scoped reconnect id (stable row order across
    attempts); ``sequence`` is the attempt-scoped trace sequence kept in the
    payload for audit correlation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cursor: int = Field(ge=1)
    run_id: UUID
    attempt_id: UUID
    sequence: int = Field(ge=0)
    event_type: str
    status: str
    occurred_at: datetime
    reason_code: str | None = None
    decision_summary: TraceDecisionSummary | None = None
    proposal_ref: str | None = None
    execution_ref: str | None = None
    audit_ref: str | None = None
    evidence: tuple[TraceEvidence, ...] = Field(default=(), max_length=32)
    query_fingerprints: tuple[str, ...] = Field(default=(), max_length=16)
    usage: _PAYLOAD_MODEL = None
    cost: _PAYLOAD_COST = None


def sse_run_event_from_trace(event: ScopedTraceEvent) -> SseRunEvent:
    """Project one audit-trace event onto the public SSE whitelist.

    Direct trace projection assumes the single-attempt scope of the event
    itself, so the cursor is the attempt-local sequence offset by one.
    """

    return SseRunEvent(
        cursor=event.sequence + 1,
        run_id=event.run_scope.run_id,
        attempt_id=event.attempt_id,
        sequence=event.sequence,
        event_type=event.event_type.value,
        status=event.status.value,
        occurred_at=event.occurred_at,
        reason_code=event.reason_code,
        decision_summary=event.decision_summary,
        proposal_ref=event.proposal_ref,
        execution_ref=event.execution_ref,
        audit_ref=event.audit_ref,
        evidence=event.evidence,
        query_fingerprints=event.query_fingerprints,
        usage=event.usage,
        cost=event.cost,
    )


def format_sse_event(event: SseRunEvent) -> str:
    """One SSE block: the run-scoped cursor is the reconnect id."""

    return (
        f"id: {event.cursor}\n"
        f"event: {event.event_type}\n"
        f"data: {event.model_dump_json()}\n\n"
    )


class RunEventSource(Protocol):
    """Read side of the product audit trail, sequenced per run."""

    async def events_after(
        self, run_id: UUID, *, after_sequence: int, limit: int = 200
    ) -> tuple[SseRunEvent, ...]: ...
