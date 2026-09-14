"""Stable SSE event surface: whitelist, wire format, and privacy guards."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from commerce_agent.api.events import (
    SSE_EVENT_TYPES,
    SseRunEvent,
    format_sse_event,
    sse_run_event_from_trace,
)
from commerce_agent.model.contracts import CostEstimate, ReportedUsage, RunScope
from commerce_agent.trace.contracts import (
    ScopedTraceEvent,
    TraceDecisionSummary,
    TraceEventType,
    TraceEvidence,
    TraceStatus,
)

WHITELIST = {
    "cursor",
    "run_id",
    "attempt_id",
    "sequence",
    "event_type",
    "status",
    "occurred_at",
    "reason_code",
    "decision_summary",
    "proposal_ref",
    "execution_ref",
    "audit_ref",
    "evidence",
    "query_fingerprints",
    "usage",
    "cost",
}


def _trace_event(sequence: int = 0) -> ScopedTraceEvent:
    config_hash = "c" * 64
    return ScopedTraceEvent(
        run_scope=RunScope(
            run_id=uuid4(),
            track="retail",
            mode="retail",
            subject_id="subject-v1",
            experiment_id="day6-api",
            config_hash=config_hash,
        ),
        attempt_id=uuid4(),
        phase="proposal",
        sequence=sequence,
        occurred_at=datetime(2026, 9, 14, 9, 0, tzinfo=UTC),
        node="create_proposal",
        event_type=TraceEventType.PROPOSAL_CREATED,
        status=TraceStatus.SUCCEEDED,
        decision_summary=TraceDecisionSummary(
            code="proposal_persisted",
            text="One reviewed proposal was persisted.",
        ),
        config_hash=config_hash,
        usage=ReportedUsage(
            status="reported",
            prompt_tokens=100,
            cache_hit_tokens=80,
            cache_miss_tokens=20,
            completion_tokens=10,
            reasoning_tokens=0,
            total_tokens=110,
        ),
        cost=CostEstimate(
            status="estimated",
            price_snapshot_id="deepseek-flash-usd-2026-09-12",
            currency="USD",
            amount=Decimal("0.000015"),
            price_band="off_peak",
        ),
        evidence=(
            TraceEvidence(
                kind="query", ref="query:" + "a" * 64, digest="b" * 64
            ),
        ),
        query_fingerprints=("a" * 64,),
        proposal_ref="proposal:00000000-0000-0000-0000-000000000203:1",
    )


def test_sse_surface_is_exactly_the_product_trace_vocabulary() -> None:
    assert SSE_EVENT_TYPES == {item.value for item in TraceEventType}


def test_payload_keys_are_exactly_the_public_whitelist() -> None:
    event = sse_run_event_from_trace(_trace_event())
    assert set(SseRunEvent.model_fields) == WHITELIST
    payload = event.model_dump(mode="json")
    assert set(payload) == WHITELIST


def test_payload_excludes_graph_coupling_and_private_vocabulary() -> None:
    event = sse_run_event_from_trace(_trace_event())
    payload = json.dumps(event.model_dump(mode="json"))
    assert "node" not in event.model_dump()
    assert "phase" not in event.model_dump()
    for forbidden in ("seller", "hmac", "grant", "signature", "nonce", "password"):
        assert forbidden not in payload.lower().replace("_", "_")


def test_wire_format_is_id_event_data_block() -> None:
    event = sse_run_event_from_trace(_trace_event(sequence=3))
    assert event.cursor == 4  # single-attempt projection: sequence + 1
    wire = format_sse_event(event)
    assert wire.startswith("id: 4\nevent: proposal_created\ndata: ")
    assert wire.endswith("\n\n")
    data_line = next(line for line in wire.splitlines() if line.startswith("data: "))
    assert set(json.loads(data_line[len("data: "):])) == WHITELIST


def test_minimal_event_roundtrips_without_optional_payload() -> None:
    config_hash = "c" * 64
    bare = ScopedTraceEvent(
        run_scope=RunScope(
            run_id=uuid4(),
            track="retail",
            mode="retail",
            subject_id="subject-v1",
            experiment_id="day6-api",
            config_hash=config_hash,
        ),
        attempt_id=uuid4(),
        phase="clarify",
        sequence=0,
        occurred_at=datetime(2026, 9, 14, 9, 0, tzinfo=UTC),
        node="clarify",
        event_type=TraceEventType.CLARIFICATION_REQUESTED,
        status=TraceStatus.SUCCEEDED,
        config_hash=config_hash,
    )
    event = sse_run_event_from_trace(bare)
    assert event.event_type == "clarification_requested"
    assert event.decision_summary is None
    assert event.usage is None and event.cost is None
    assert set(json.loads(format_sse_event(event).split("data: ", 1)[1])) == WHITELIST
