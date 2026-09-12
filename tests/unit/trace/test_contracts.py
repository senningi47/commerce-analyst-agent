from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from commerce_agent.model.contracts import RunScope
from commerce_agent.trace.contracts import (
    ScopedTraceEvent,
    TraceDecisionSummary,
    TraceEventType,
    TraceStatus,
)
from commerce_agent.trace.errors import TraceContractError
from commerce_agent.trace.module import validate_trace_event

TEST_SCOPE = RunScope(
    run_id=UUID(int=100),
    track="retail",
    mode="retail",
    subject_id="day4-scenario",
    experiment_id="day4-product",
    config_hash="a" * 64,
)
TEST_ATTEMPT = UUID(int=101)


def scoped_event(
    *, sequence: int = 0, decision_summary: TraceDecisionSummary | None = None
) -> ScopedTraceEvent:
    return ScopedTraceEvent(
        run_scope=TEST_SCOPE,
        attempt_id=TEST_ATTEMPT,
        phase="investigation",
        sequence=sequence,
        occurred_at=datetime(2026, 9, 6, 4, 0, tzinfo=UTC),
        node="plan",
        event_type=TraceEventType.INVESTIGATION_PLAN_ACCEPTED,
        status=TraceStatus.SUCCEEDED,
        decision_summary=decision_summary,
        config_hash="a" * 64,
    )


def test_trace_contract_rejects_private_fields() -> None:
    with pytest.raises(ValidationError):
        ScopedTraceEvent.model_validate(scoped_event().model_dump() | {"signature": "private"})


@pytest.mark.parametrize(
    "summary",
    [
        "postgresql://writer:secret@127.0.0.1/db",
        "sk-private-token",
        "raw_seller_id=abc123",
        "reasoning_content=private",
        "approval_nonce=AQID",
    ],
)
def test_trace_rejects_sensitive_summary(summary: str) -> None:
    event = scoped_event(
        decision_summary=TraceDecisionSummary(code="reviewed", text=summary)
    )
    with pytest.raises(TraceContractError) as caught:
        validate_trace_event(event)
    assert caught.value.reason_code == "trace_sensitive_content"
