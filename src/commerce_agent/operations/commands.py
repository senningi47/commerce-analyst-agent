"""Closed command vocabulary for Day 4 Product mutations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from commerce_agent.operations import contracts as _contracts
from commerce_agent.operations.contracts import (
    AlertBacktestRef,
    AlertHitRef,
    EvidenceRef,
    FrozenContract,
    InvestigationStatus,
    InvestigationTaskRef,
    MetricRef,
    RiskDisposition,
    SellerRef,
)

Priority = Literal["low", "medium", "high"]


def _validate_evidence(value: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
    if len({(item.kind, item.ref, item.digest) for item in value}) != len(value):
        raise ValueError("evidence references must be unique")
    return value


def _validate_finite_decimal(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("decimal must be finite")
    if value < 0 or value > 1:
        raise ValueError("decimal ratio must be between zero and one")
    if max(0, -value.as_tuple().exponent) > 6:
        raise ValueError("decimal scale exceeds six places")
    return value


class EvidenceCommand(FrozenContract):
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=32)

    _unique_evidence = field_validator("evidence_refs")(_validate_evidence)


class NewTargetCommand(EvidenceCommand):
    expected_target_version: Literal[0] = 0


class ExistingTaskCommand(EvidenceCommand):
    task_ref: InvestigationTaskRef
    expected_target_version: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_exact_version(self) -> ExistingTaskCommand:
        if self.task_ref.version != self.expected_target_version:
            raise ValueError("task ref version must equal expected target version")
        return self


class OpenSellerRiskCase(NewTargetCommand):
    type: Literal["open_seller_risk_case"]
    seller_ref: SellerRef
    observation_started_at: datetime
    observation_ended_at: datetime
    metric_ref: MetricRef
    numerator: int = Field(ge=0)
    denominator: int = Field(gt=0)
    observed: Decimal
    threshold: Decimal
    title: str = Field(min_length=1, max_length=200)
    priority: Priority

    _finite_observed = field_validator("observed")(_validate_finite_decimal)
    _finite_threshold = field_validator("threshold")(_validate_finite_decimal)

    @field_validator("observation_started_at", "observation_ended_at")
    @classmethod
    def validate_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone-aware observation required")
        return value

    @model_validator(mode="after")
    def validate_observation(self) -> OpenSellerRiskCase:
        if self.observation_started_at >= self.observation_ended_at:
            raise ValueError("observation range must be increasing")
        if self.numerator > self.denominator:
            raise ValueError("numerator cannot exceed denominator")
        return self


class CreateInvestigationTask(NewTargetCommand):
    type: Literal["create_investigation_task"]
    title: str = Field(min_length=1, max_length=200)
    priority: Priority
    public_summary: str = Field(min_length=1, max_length=2_000)


class CreateInvestigationFromAlertHit(NewTargetCommand):
    type: Literal["create_investigation_from_alert_hit"]
    alert_hit_ref: AlertHitRef
    title: str = Field(min_length=1, max_length=200)
    priority: Priority


class CreateAndEnableMetricAlertRule(NewTargetCommand):
    type: Literal["create_and_enable_metric_alert_rule"]
    alert_backtest_ref: AlertBacktestRef
    metric_ref: MetricRef
    grain: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    window: Literal["week", "month"]
    comparator: Literal["greater_than", "greater_than_or_equal"]
    threshold: Decimal
    minimum_denominator: int = Field(gt=0, le=1_000_000_000)
    filter_refs: tuple[str, ...] = Field(max_length=32)

    _finite_threshold = field_validator("threshold")(_validate_finite_decimal)

    @field_validator("filter_refs")
    @classmethod
    def validate_filter_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(not item or len(item) > 256 for item in value):
            raise ValueError("filter references must be unique and non-empty")
        return value


class AssignInvestigation(ExistingTaskCommand):
    type: Literal["assign_investigation"]
    assignee_ref: str = Field(min_length=1, max_length=256)


_ALLOWED_TRANSITIONS = {
    (InvestigationStatus.OPEN, InvestigationStatus.IN_PROGRESS),
    (InvestigationStatus.OPEN, InvestigationStatus.BLOCKED),
    (InvestigationStatus.IN_PROGRESS, InvestigationStatus.BLOCKED),
    (InvestigationStatus.IN_PROGRESS, InvestigationStatus.RESOLVED),
    (InvestigationStatus.BLOCKED, InvestigationStatus.IN_PROGRESS),
    (InvestigationStatus.BLOCKED, InvestigationStatus.RESOLVED),
}


class TransitionInvestigation(ExistingTaskCommand):
    type: Literal["transition_investigation"]
    from_status: InvestigationStatus
    to_status: InvestigationStatus
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")

    @model_validator(mode="after")
    def validate_transition(self) -> TransitionInvestigation:
        if (self.from_status, self.to_status) not in _ALLOWED_TRANSITIONS:
            raise ValueError("transition is not allowed")
        return self


class AddInvestigationConclusion(ExistingTaskCommand):
    type: Literal["add_investigation_conclusion"]
    conclusion_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    conclusion_summary: str = Field(min_length=1, max_length=2_000)


class CloseInvestigation(ExistingTaskCommand):
    type: Literal["close_investigation"]
    conclusion_ref: str = Field(min_length=1, max_length=256)
    risk_disposition: RiskDisposition | None = None


OperationCommand = Annotated[
    OpenSellerRiskCase
    | CreateInvestigationTask
    | CreateInvestigationFromAlertHit
    | CreateAndEnableMetricAlertRule
    | AssignInvestigation
    | TransitionInvestigation
    | AddInvestigationConclusion
    | CloseInvestigation,
    Field(discriminator="type"),
]


_contracts.ProposeRequest.model_rebuild(_types_namespace={"OperationCommand": OperationCommand})
