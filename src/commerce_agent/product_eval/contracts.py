"""Immutable deterministic Product scenario contracts."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from commerce_agent.orchestration.contracts import ClarificationSlot

FailurePoint = Literal[
    "before_proposal_persist",
    "after_proposal_commit_response_lost",
    "before_execute_call",
    "after_execute_commit_response_lost",
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ScriptedClarification(_FrozenModel):
    slot: ClarificationSlot
    response: str = Field(min_length=1, max_length=1_000)


class ScenarioActorStep(_FrozenModel):
    action: Literal["approve_execute", "reject"]
    actor_ref: str = Field(min_length=1, max_length=128)
    proposal_index: int = Field(ge=0, le=16)


class OperationExpectation(_FrozenModel):
    command_type: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    terminal_status: Literal["succeeded", "rejected"]
    count: int = Field(ge=0, le=16)


class ReadbackExpectation(_FrozenModel):
    sql: str = Field(min_length=1, max_length=100_000)
    expected_columns: tuple[str, ...] = Field(min_length=1, max_length=32)
    minimum_rows: int = Field(default=1, ge=0, le=1_000)


class AuditExpectation(_FrozenModel):
    event_type: Literal["operation_executed"]
    count: int = Field(ge=0, le=16)


class ScenarioCheck(_FrozenModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    passed: bool
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")


class ProductScenario(_FrozenModel):
    scenario_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,127}-v[0-9]+$")
    fixture_revision: str = Field(pattern=r"^day4-(?:development|regression)-v[0-9]+$")
    visibility: Literal["development", "regression"]
    initial_question: str = Field(min_length=1, max_length=4_000)
    scripted_clarifications: tuple[ScriptedClarification, ...]
    analyst_actor_ref: str = Field(min_length=1, max_length=128)
    approver_actor_ref: str = Field(min_length=1, max_length=128)
    actor_steps: tuple[ScenarioActorStep, ...]
    clock_steps: tuple[datetime, ...] = Field(min_length=1, max_length=64)
    ops_initial_state: dict[str, JsonValue]
    reset_manifest_revision: str = Field(pattern=r"^scenario-reset-v[0-9]+$")
    gold_tables: tuple[str, ...]
    gold_columns: tuple[str, ...]
    gold_values: tuple[str, ...]
    reference_query_assertions: tuple[ReadbackExpectation, ...] = ()
    database_assertions: tuple[ScenarioCheck, ...] = ()
    required_clarification_slots: tuple[ClarificationSlot, ...]
    forbidden_clarification_slots: tuple[ClarificationSlot, ...]
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    operation_expectations: tuple[OperationExpectation, ...]
    readback_expectations: tuple[ReadbackExpectation, ...]
    audit_expectations: tuple[AuditExpectation, ...]
    allowed_failure_script: tuple[FailurePoint, ...]
    expected_terminal_status: Literal["completed", "stopped"]
    decimal_tolerance: Decimal = Field(default=Decimal("0.01"), ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_scenario(self) -> "ProductScenario":
        if bool(self.reference_query_assertions) == bool(self.database_assertions):
            raise ValueError("exactly one assertion source is required")
        required = set(self.required_clarification_slots)
        forbidden = set(self.forbidden_clarification_slots)
        if required & forbidden:
            raise ValueError("clarification slots cannot be required and forbidden")
        if len({item.slot for item in self.scripted_clarifications}) != len(
            self.scripted_clarifications
        ):
            raise ValueError("scripted clarification slots must be unique")
        if len(set(self.allowed_failure_script)) != len(self.allowed_failure_script):
            raise ValueError("failure script points must be unique")
        for value in self.clock_steps:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("scenario clocks must be timezone-aware")
        return self


class ProductScenarioResult(_FrozenModel):
    scenario_id: str
    status: Literal["passed", "failed"]
    checks: tuple[ScenarioCheck, ...]
    asked_slots: tuple[ClarificationSlot, ...] = ()
    invalid_clarification_count: int = Field(default=0, ge=0)
    silent_default_count: int = Field(default=0, ge=0)
    proposal_count: int = Field(default=0, ge=0)
    approval_count: int = Field(default=0, ge=0)
    execution_count: int = Field(default=0, ge=0)
    audit_event_count: int = Field(default=0, ge=0)
    readback_count: int = Field(default=0, ge=0)
    successful_business_write_count: int = Field(default=0, ge=0)
    unauthorized_business_write_count: int = Field(default=0, ge=0)
    successful_execution_audit_count: int = Field(default=0, ge=0)
    sensitivity_comparison_present: bool = False
    claims: tuple[str, ...] = ()
    unique_proposal_count: int = Field(default=0, ge=0)
    unique_business_write_count: int = Field(default=0, ge=0)
    duplicate_audit_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_status(self) -> "ProductScenarioResult":
        if self.status == "passed" and not all(item.passed for item in self.checks):
            raise ValueError("passed scenario requires every check to pass")
        return self
