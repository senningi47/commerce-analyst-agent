"""Closed public contracts for the three orchestration tracks."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from commerce_agent.context_builder.contracts import ContextDatum, TypedErrorDatum
from commerce_agent.model.contracts import Cost, FinalOutput, RunScope, Usage
from commerce_agent.operations.contracts import ProposalRef


class StopKind(StrEnum):
    """Closed reasons an orchestration track may stop."""

    BUDGET_EXHAUSTED = "budget_exhausted"
    NO_PROGRESS = "no_progress"
    UNSAFE = "unsafe"
    INSUFFICIENT_DATA = "insufficient_data"
    COMPLETED = "completed"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


class StopOutcome(BaseModel, frozen=True, extra="forbid"):
    """Stable, provider-neutral stop detail shared by all tracks."""

    kind: StopKind
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    evidence_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    retryable: bool


class RetailRunRequest(BaseModel, frozen=True, extra="forbid"):
    """Input accepted only by the Retail orchestration entrypoint."""

    run_scope: RunScope
    attempt_id: UUID
    current_input: ContextDatum
    confirmed_facts: tuple[ContextDatum, ...] = ()
    evidence: tuple[ContextDatum, ...] = ()
    latest_error: TypedErrorDatum | None = None

    @model_validator(mode="after")
    def validate_retail_scope(self) -> "RetailRunRequest":
        if (self.run_scope.track, self.run_scope.mode) != ("retail", "retail"):
            raise ValueError("RetailRunRequest requires a retail scope")
        return self


class ClarificationSlot(StrEnum):
    GMV_METRIC_DEFINITION = "gmv_metric_definition"
    VALID_ORDER_STATUSES = "valid_order_statuses"
    TIME_FIELD = "time_field"
    TIME_RANGE = "time_range"
    ANALYSIS_GRAIN = "analysis_grain"
    MINIMUM_ORDER_COUNT = "minimum_order_count"
    ANOMALY_THRESHOLD = "anomaly_threshold"
    BUSINESS_VALUE = "business_value"


class ClarificationItem(BaseModel, frozen=True, extra="forbid"):
    slot: ClarificationSlot
    question: str = Field(min_length=1, max_length=1_000)
    allowed_values: tuple[str, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_allowed_values(self) -> "ClarificationItem":
        if len(set(self.allowed_values)) != len(self.allowed_values):
            raise ValueError("clarification allowed values must be unique")
        return self


class ClarificationRequest(BaseModel, frozen=True, extra="forbid"):
    type: Literal["clarification_request"] = "clarification_request"
    conversation_id: str = Field(min_length=1, max_length=128)
    request_id: UUID
    items: tuple[ClarificationItem, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_unique_slots(self) -> "ClarificationRequest":
        slots = tuple(item.slot for item in self.items)
        if len(set(slots)) != len(slots):
            raise ValueError("clarification slots must be unique")
        return self


class InvestigationTimeRange(BaseModel, frozen=True, extra="forbid"):
    started_at: datetime
    ended_at: datetime

    @model_validator(mode="after")
    def validate_range(self) -> "InvestigationTimeRange":
        for value in (self.started_at, self.ended_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("investigation time range requires timezone")
        if self.started_at >= self.ended_at:
            raise ValueError("investigation time range must be increasing")
        return self


class ConfirmedInvestigationFacts(BaseModel, frozen=True, extra="forbid"):
    gmv_metric_definition: Literal["item_amount", "payment_amount"]
    valid_order_statuses: tuple[str, ...] = Field(min_length=1, max_length=16)
    time_field: Literal[
        "order_purchase_timestamp", "order_approved_at", "order_delivered_customer_date"
    ]
    time_range: InvestigationTimeRange
    analysis_grain: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    minimum_order_count: int = Field(gt=0, le=1_000_000_000)
    anomaly_threshold: Decimal = Field(ge=0, le=1, allow_inf_nan=False, decimal_places=6)
    business_value_refs: tuple[str, ...] = Field(default=(), max_length=32)


class QueryPlanStep(BaseModel, frozen=True, extra="forbid"):
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")
    kind: Literal["baseline", "drilldown"]
    question: str = Field(min_length=1, max_length=1_000)
    measures: tuple[str, ...] = Field(min_length=1, max_length=16)
    grains: tuple[str, ...] = Field(min_length=1, max_length=8)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    reconciliation_rule_refs: tuple[str, ...] = Field(min_length=1, max_length=16)


class InvestigationPlan(BaseModel, frozen=True, extra="forbid"):
    type: Literal["investigation_plan"] = "investigation_plan"
    conversation_id: str = Field(min_length=1, max_length=128)
    plan_id: UUID
    confirmed_facts: ConfirmedInvestigationFacts
    steps: tuple[QueryPlanStep, ...] = Field(min_length=2, max_length=12)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=64)
    reconciliation_rule_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    stop_conditions: tuple[str, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_plan_shape(self) -> "InvestigationPlan":
        if sum(step.kind == "baseline" for step in self.steps) != 1:
            raise ValueError("plan requires exactly one baseline step")
        if not any(step.kind == "drilldown" for step in self.steps):
            raise ValueError("plan requires at least one drilldown step")
        step_ids = tuple(step.step_id for step in self.steps)
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("plan step IDs must be unique")
        available_evidence = set(self.evidence_refs)
        available_rules = set(self.reconciliation_rule_refs)
        if any(not set(step.evidence_refs) <= available_evidence for step in self.steps):
            raise ValueError("plan step evidence must be declared")
        if any(
            not set(step.reconciliation_rule_refs) <= available_rules
            for step in self.steps
        ):
            raise ValueError("plan step reconciliation rules must be declared")
        return self


class InvestigationEvidence(BaseModel, frozen=True, extra="forbid"):
    evidence_id: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{2,255}$")
    kind: Literal["knowledge", "query", "reconciliation", "operation"]
    summary: str = Field(min_length=1, max_length=2_000)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class InvestigationClaim(BaseModel, frozen=True, extra="forbid"):
    claim_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")
    text: str = Field(min_length=1, max_length=2_000)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)


class InvestigationLimitation(BaseModel, frozen=True, extra="forbid"):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    summary: str = Field(min_length=1, max_length=1_000)


class SensitivityComparison(BaseModel, frozen=True, extra="forbid"):
    all_sellers_evidence_ref: str = Field(min_length=1, max_length=256)
    selected_risk_evidence_ref: str = Field(min_length=1, max_length=256)
    summary: str = Field(min_length=1, max_length=2_000)


class InvestigationReport(BaseModel, frozen=True, extra="forbid"):
    type: Literal["investigation_report"] = "investigation_report"
    conversation_id: str = Field(min_length=1, max_length=128)
    report_id: UUID
    claims: tuple[InvestigationClaim, ...] = Field(min_length=1, max_length=32)
    available_evidence: tuple[InvestigationEvidence, ...] = Field(min_length=1, max_length=64)
    limitations: tuple[InvestigationLimitation, ...] = Field(default=(), max_length=16)
    reconciliation_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    proposal_refs: tuple[ProposalRef, ...] = Field(default=(), max_length=8)
    selected_risk_status: Literal["confirmed", "dismissed", "inconclusive"] | None = None
    sensitivity_comparison: SensitivityComparison | None = None

    @model_validator(mode="after")
    def validate_evidence_and_sensitivity(self) -> "InvestigationReport":
        evidence_ids = {item.evidence_id for item in self.available_evidence}
        if any(not set(claim.evidence_refs) <= evidence_ids for claim in self.claims):
            raise ValueError("claim evidence must be available")
        if not set(self.reconciliation_refs) <= evidence_ids:
            raise ValueError("reconciliation evidence must be available")
        if self.selected_risk_status is not None and self.sensitivity_comparison is None:
            raise ValueError("risk-filtered report requires sensitivity comparison")
        if self.sensitivity_comparison is not None and not {
            self.sensitivity_comparison.all_sellers_evidence_ref,
            self.sensitivity_comparison.selected_risk_evidence_ref,
        } <= evidence_ids:
            raise ValueError("sensitivity evidence must be available")
        return self


RetailTerminal = Annotated[
    ClarificationRequest | InvestigationReport,
    Field(discriminator="type"),
]


class RetailRunOutcome(BaseModel, frozen=True, extra="forbid"):
    """Terminal public result from the Retail orchestration entrypoint."""

    status: Literal["needs_input", "completed", "stopped"]
    terminal: RetailTerminal | None = None
    stop: StopOutcome | None = None
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    prompt_policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rendered_prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_id: UUID

    @model_validator(mode="after")
    def validate_terminal_value(self) -> "RetailRunOutcome":
        if self.status == "needs_input" and not isinstance(
            self.terminal, ClarificationRequest
        ):
            raise ValueError("needs_input status requires clarification terminal")
        if self.status == "completed" and not isinstance(
            self.terminal, InvestigationReport
        ):
            raise ValueError("completed status requires report terminal")
        if self.status == "stopped" and (self.stop is None or self.terminal is not None):
            raise ValueError("stopped status requires only stop")
        if self.status != "stopped" and self.stop is not None:
            raise ValueError("non-stopped status forbids stop")
        return self


class BirdARunRequest(BaseModel, frozen=True, extra="forbid"):
    """Input accepted only by the BirdA orchestration entrypoint."""

    run_scope: RunScope
    attempt_id: UUID
    current_input: ContextDatum
    confirmed_facts: tuple[ContextDatum, ...] = ()
    evidence: tuple[ContextDatum, ...] = ()
    latest_error: TypedErrorDatum | None = None
    max_model_calls: int = Field(default=6, ge=1, le=60)
    max_tool_calls: int = Field(default=8, ge=0, le=60)

    @model_validator(mode="after")
    def validate_bird_a_scope(self) -> "BirdARunRequest":
        if (self.run_scope.track, self.run_scope.mode) != ("bird", "a"):
            raise ValueError("BirdARunRequest requires a BirdA scope")
        return self


class BirdARunOutcome(BaseModel, frozen=True, extra="forbid"):
    """Terminal public result from the BirdA orchestration entrypoint."""

    status: Literal["completed", "stopped"]
    final_output: FinalOutput | None = None
    stop: StopOutcome | None = None
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    prompt_policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rendered_prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_id: UUID

    @model_validator(mode="after")
    def validate_terminal_value(self) -> "BirdARunOutcome":
        if (self.final_output is None) == (self.stop is None):
            raise ValueError("exactly one of final_output or stop must be present")
        if self.status == "completed" and self.final_output is None:
            raise ValueError("completed outcomes require final_output")
        if self.status == "stopped" and self.stop is None:
            raise ValueError("stopped outcomes require stop")
        return self


class BirdCRequest(BaseModel, frozen=True, extra="forbid"):
    """Input accepted only by the BirdC responder entrypoint."""

    run_scope: RunScope
    attempt_id: UUID
    current_phase: ContextDatum
    confirmed_facts: tuple[ContextDatum, ...] = ()
    evidence: tuple[ContextDatum, ...] = ()
    latest_error: TypedErrorDatum | None = None

    @model_validator(mode="after")
    def validate_bird_c_scope(self) -> "BirdCRequest":
        if (self.run_scope.track, self.run_scope.mode) != ("bird", "c"):
            raise ValueError("BirdCRequest requires a BirdC scope")
        return self


class AskUserCandidate(BaseModel, frozen=True, extra="forbid"):
    """BirdC candidate asking the evaluator for one clarification."""

    type: Literal["ask_user"]
    question: str = Field(min_length=1, max_length=4_000)


class SubmitSqlCandidate(BaseModel, frozen=True, extra="forbid"):
    """BirdC candidate submitting one SQL statement."""

    type: Literal["submit_sql"]
    sql: str = Field(min_length=1, max_length=100_000)


class TextCandidate(BaseModel, frozen=True, extra="forbid"):
    """BirdC candidate: the model answered in prose.

    Official ADK semantics (run f verdict 2026-09-15): a non-function-call
    response ENDS the runner invocation instead of failing the episode; the
    orchestrator's next phase message continues with the text in session
    memory."""

    type: Literal["text"]
    content: str = Field(min_length=1, max_length=100_000)


BirdCCandidate = Annotated[
    AskUserCandidate | SubmitSqlCandidate | TextCandidate,
    Field(discriminator="type"),
]


class BirdCResponse(BaseModel, frozen=True, extra="forbid"):
    """Single-exchange public result from the BirdC responder."""

    candidate: BirdCCandidate
    usage: Usage
    cost: Cost
    prompt_policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rendered_prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_id: UUID
