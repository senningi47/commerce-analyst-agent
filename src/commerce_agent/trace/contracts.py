"""Closed, provider-neutral Trace contracts."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from commerce_agent.model.contracts import Cost, RunScope, Usage


class TraceEventType(StrEnum):
    CLARIFICATION_REQUESTED = "clarification_requested"
    INVESTIGATION_PLAN_ACCEPTED = "investigation_plan_accepted"
    SQL_GENERATED = "sql_generated"
    SQL_REPAIRED = "sql_repaired"
    QUERY_EXECUTED = "query_executed"
    QUERY_RECONCILED = "query_reconciled"
    PROPOSAL_CREATED = "proposal_created"
    DECISION_RECORDED = "decision_recorded"
    EXECUTION_COMPLETED = "execution_completed"
    REPORT_COMPLETED = "report_completed"
    RECOVERY_APPLIED = "recovery_applied"


class TraceStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STOPPED = "stopped"


class TraceDecisionSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    text: str = Field(min_length=1, max_length=2_000)


class TraceEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,63}$")
    ref: str = Field(min_length=1, max_length=256)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ScopedTraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_scope: RunScope
    attempt_id: UUID
    phase: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    sequence: int = Field(ge=0)
    occurred_at: datetime
    node: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    event_type: TraceEventType
    status: TraceStatus
    decision_summary: TraceDecisionSummary | None = None
    prompt_policy_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rendered_prompt_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    tool_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    context_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    usage: Annotated[Usage | None, Field(discriminator="status")] = None
    cost: Annotated[Cost | None, Field(discriminator="status")] = None
    evidence: tuple[TraceEvidence, ...] = Field(default=(), max_length=32)
    query_fingerprints: tuple[str, ...] = Field(default=(), max_length=16)
    reason_code: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]{2,127}$"
    )
    proposal_ref: str | None = Field(default=None, min_length=1, max_length=256)
    execution_ref: str | None = Field(default=None, min_length=1, max_length=256)
    audit_ref: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone-aware trace time required")
        return value

    @field_validator("query_fingerprints")
    @classmethod
    def validate_fingerprints(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("query fingerprints must be unique")
        if any(len(item) != 64 or any(c not in "0123456789abcdef" for c in item) for item in value):
            raise ValueError("query fingerprints must be lowercase SHA-256 values")
        return value
