"""Provider-neutral immutable contracts for controlled Product operations."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from commerce_agent.operations.commands import OperationCommand


class FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value


class ActorRole(StrEnum):
    ANALYST = "analyst"
    APPROVER = "approver"
    DATA_ADMIN = "data_admin"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class ExecutionStatus(StrEnum):
    NOT_STARTED = "not_started"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_FINAL = "failed_final"


class InvestigationStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    RESOLVED = "resolved"
    CLOSED = "closed"


class RiskDisposition(StrEnum):
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    INCONCLUSIVE = "inconclusive"


class AlertMetric(StrEnum):
    LATE_DELIVERY_RATE = "late_delivery_rate"
    LOW_RATING_RATE = "low_rating_rate"
    CANCELLATION_RATE = "cancellation_rate"


class CalendarWindow(StrEnum):
    WEEK = "week"
    MONTH = "month"


class AlertComparator(StrEnum):
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"


class ActorContext(FrozenContract):
    actor_id: UUID
    role: ActorRole
    authentication_ref: str = Field(min_length=1, max_length=256)
    authenticated_at: datetime

    _validate_authenticated_at = field_validator("authenticated_at")(_require_aware)


class EvidenceRef(FrozenContract):
    kind: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,63}$")
    ref: str = Field(min_length=1, max_length=256)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProposalRef(FrozenContract):
    proposal_id: UUID
    version: int = Field(ge=1)


class MetricRef(FrozenContract):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")
    revision: str = Field(min_length=1, max_length=128)


class SellerRef(FrozenContract):
    namespace: Literal["product:seller-target"]
    token: str = Field(min_length=8, max_length=4096)


class SellerTargetRequest(FrozenContract):
    metric_ref: MetricRef
    observation_started_at: datetime
    observation_ended_at: datetime
    anomaly_threshold: Decimal = Field(ge=0, le=1, allow_inf_nan=False, decimal_places=6)
    minimum_denominator: int = Field(gt=0, le=1_000_000_000)
    status_filters: tuple[str, ...] = Field(min_length=1, max_length=16)
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("observation_started_at", "observation_ended_at")
    @classmethod
    def validate_observation_time(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def validate_observation_range(self) -> SellerTargetRequest:
        if self.observation_started_at >= self.observation_ended_at:
            raise ValueError("observation range must be increasing")
        if len(set(self.status_filters)) != len(self.status_filters):
            raise ValueError("status filters must be unique")
        return self


class SellerCandidateEvidence(FrozenContract):
    seller_ref: SellerRef
    numerator: int = Field(ge=0)
    denominator: int = Field(gt=0)
    normalized_value: Decimal = Field(ge=0, le=1, allow_inf_nan=False, decimal_places=6)
    observation_started_at: datetime
    observation_ended_at: datetime
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    _validate_started_at = field_validator("observation_started_at")(_require_aware)
    _validate_ended_at = field_validator("observation_ended_at")(_require_aware)


class InvestigationTaskRef(FrozenContract):
    task_id: UUID
    version: int = Field(ge=0)


class AlertBacktestRef(FrozenContract):
    backtest_id: UUID
    rule_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AlertBacktestRequest(FrozenContract):
    metric: AlertMetric
    metric_revision: str = Field(min_length=1, max_length=128)
    grain: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    window: CalendarWindow
    comparator: AlertComparator
    threshold: Decimal = Field(ge=0, le=1, allow_inf_nan=False, decimal_places=6)
    minimum_denominator: int = Field(gt=0, le=1_000_000_000)
    started_at: datetime
    ended_at: datetime
    filter_refs: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("started_at", "ended_at")
    @classmethod
    def validate_range_time(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def validate_calendar_range(self) -> AlertBacktestRequest:
        if self.started_at >= self.ended_at:
            raise ValueError("backtest range must be increasing")
        if self.ended_at - self.started_at > timedelta(days=366):
            raise ValueError("backtest range exceeds one year")
        for value in (self.started_at, self.ended_at):
            if any((value.hour, value.minute, value.second, value.microsecond)):
                raise ValueError("calendar window boundary must be midnight")
            if self.window is CalendarWindow.MONTH and value.day != 1:
                raise ValueError("month window boundary must be the first day")
            if self.window is CalendarWindow.WEEK and value.weekday() != 0:
                raise ValueError("week window boundary must be Monday")
        if len(set(self.filter_refs)) != len(self.filter_refs):
            raise ValueError("filter references must be unique")
        if any(not item or len(item) > 256 for item in self.filter_refs):
            raise ValueError("filter reference is invalid")
        return self


class AlertWindowResult(FrozenContract):
    window_started_at: datetime
    window_ended_at: datetime
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    normalized_value: Decimal | None = Field(
        default=None, ge=0, le=1, allow_inf_nan=False, decimal_places=6
    )
    complete: bool
    excluded_reason: Literal[
        "zero_denominator", "below_minimum_denominator", "incomplete_window"
    ] | None = None
    hit: bool


class AlertBacktestSnapshot(FrozenContract):
    backtest_ref: AlertBacktestRef
    request: AlertBacktestRequest
    windows: tuple[AlertWindowResult, ...]
    completed_at: datetime

    _validate_completed_at = field_validator("completed_at")(_require_aware)


class AlertHitRef(FrozenContract):
    hit_id: UUID
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class CommandPreview(FrozenContract):
    command_type: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    title: str = Field(min_length=1, max_length=200)
    public_summary: str = Field(min_length=1, max_length=2_000)
    target_versions: dict[str, int] = Field(min_length=1, max_length=8)
    affected_rows: int = Field(ge=0, le=8)

    @field_validator("target_versions")
    @classmethod
    def validate_target_versions(cls, value: dict[str, int]) -> dict[str, int]:
        if any(version < 0 for version in value.values()):
            raise ValueError("target version must be non-negative")
        return value


class ExecutionGrant(FrozenContract):
    proposal_id: UUID
    proposal_version: int = Field(ge=1)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requester_id: UUID
    approver_id: UUID
    target_versions: dict[str, int] = Field(min_length=1, max_length=8)
    expires_at: datetime
    nonce: str = Field(min_length=4, max_length=128)
    key_version: int = Field(ge=1)
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")

    _validate_expires_at = field_validator("expires_at")(_require_aware)


class ProposeRequest(FrozenContract):
    actor: ActorContext
    command: OperationCommand
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=32)
    idempotency_key: str = Field(
        min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]+$"
    )
    revises: ProposalRef | None = None

    @field_validator("evidence_refs")
    @classmethod
    def unique_evidence(cls, value: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
        if len({(item.kind, item.ref, item.digest) for item in value}) != len(value):
            raise ValueError("evidence references must be unique")
        return value


class ProposalSnapshot(FrozenContract):
    proposal_ref: ProposalRef
    status: ProposalStatus
    requester_id: UUID
    command_type: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview: CommandPreview
    created_at: datetime
    expires_at: datetime

    _validate_created_at = field_validator("created_at")(_require_aware)
    _validate_expires_at = field_validator("expires_at")(_require_aware)

    @model_validator(mode="after")
    def validate_time_range(self) -> ProposalSnapshot:
        if self.created_at >= self.expires_at:
            raise ValueError("proposal expiry must follow creation")
        return self


class DecisionRequest(FrozenContract):
    actor: ActorContext
    proposal_ref: ProposalRef
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, min_length=1, max_length=1_000)


class DecisionReceipt(FrozenContract):
    proposal_ref: ProposalRef
    status: Literal[ProposalStatus.APPROVED, ProposalStatus.REJECTED]
    approver_id: UUID
    decided_at: datetime
    reason: str | None = Field(default=None, min_length=1, max_length=1_000)
    grant: ExecutionGrant | None = None

    _validate_decided_at = field_validator("decided_at")(_require_aware)

    @model_validator(mode="after")
    def validate_grant_consistency(self) -> DecisionReceipt:
        if self.status == ProposalStatus.APPROVED and self.grant is None:
            raise ValueError("approved decision requires grant")
        if self.status == ProposalStatus.REJECTED and self.grant is not None:
            raise ValueError("rejected decision forbids grant")
        return self


class ExecuteRequest(FrozenContract):
    actor: ActorContext
    grant: ExecutionGrant


class ExecutionReceipt(FrozenContract):
    execution_id: UUID
    proposal_ref: ProposalRef
    status: Literal[ExecutionStatus.SUCCEEDED]
    command_type: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    before_version: int = Field(ge=0)
    after_version: int = Field(ge=1)
    committed_at: datetime
    public_summary: str = Field(min_length=1, max_length=2_000)
    audit_ref: str = Field(min_length=1, max_length=256)

    _validate_committed_at = field_validator("committed_at")(_require_aware)

    @model_validator(mode="after")
    def validate_version_advance(self) -> ExecutionReceipt:
        if self.after_version <= self.before_version:
            raise ValueError("execution must advance target version")
        return self
