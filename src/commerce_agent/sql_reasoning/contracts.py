"""Provider-neutral inputs and outputs for Product SQL reasoning."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from commerce_agent.context_builder.contracts import ContextDatum
from commerce_agent.model.contracts import Cost, ModelAttemptSummary, RunScope, Usage


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProductDbError(_FrozenModel):
    source: Literal["postgres", "query_engine"]
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    retryable: bool
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class SqlFingerprint(_FrozenModel):
    kind: Literal["structural", "execution"]
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    algorithm_version: Literal["sql-fingerprint-v1"] = "sql-fingerprint-v1"
    parser_version: str = Field(min_length=1, max_length=64)


class SqlCandidate(_FrozenModel):
    sql: str = Field(min_length=1, max_length=100_000)
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")
    repair_number: int = Field(ge=0, le=3)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    structural_fingerprint: SqlFingerprint
    execution_fingerprint: SqlFingerprint
    usage: Usage
    cost: Cost
    attempts: tuple[ModelAttemptSummary, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_fingerprint_kinds(self) -> SqlCandidate:
        if self.structural_fingerprint.kind != "structural":
            raise ValueError("structural fingerprint kind required")
        if self.execution_fingerprint.kind != "execution":
            raise ValueError("execution fingerprint kind required")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("SQL evidence references must be unique")
        return self


class SqlReasoningRequest(_FrozenModel):
    run_scope: RunScope
    attempt_id: UUID
    sequence: int = Field(ge=0)
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")
    current_input: ContextDatum
    confirmed_facts: tuple[ContextDatum, ...] = ()
    evidence: tuple[ContextDatum, ...] = Field(min_length=1, max_length=64)
    profile_key: Literal["retail"] = "retail"
    repair_number: int = Field(default=0, ge=0, le=3)
    latest_error: ProductDbError | None = None
    previous: SqlCandidate | None = None

    @model_validator(mode="after")
    def validate_repair_state(self) -> SqlReasoningRequest:
        if self.latest_error is None and (self.repair_number != 0 or self.previous is not None):
            raise ValueError("first generation cannot carry repair state")
        if self.latest_error is not None and (
            self.repair_number == 0 or self.previous is None
        ):
            raise ValueError("repair requires prior candidate and positive repair number")
        return self


class SqlReasoningSummary(_FrozenModel):
    step_id: str
    repair_number: int = Field(ge=0, le=3)
    structural_fingerprint: SqlFingerprint
    execution_fingerprint: SqlFingerprint
    evidence_refs: tuple[str, ...]
