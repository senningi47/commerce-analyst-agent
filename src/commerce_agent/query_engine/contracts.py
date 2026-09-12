from decimal import Decimal
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, Field, computed_field, model_validator

QueryScalar: TypeAlias = str | int | float | bool | None


class QueryRequest(BaseModel, frozen=True):
    sql: str = Field(min_length=1, max_length=100_000)


class ExplainSummary(BaseModel, frozen=True):
    total_cost: float = Field(ge=0)
    plan_rows: int = Field(ge=0)


class QueryResult(BaseModel, frozen=True):
    columns: list[str]
    rows: list[dict[str, QueryScalar]]
    explain: ExplainSummary | None = None
    truncated: bool = False

    @computed_field
    @property
    def row_count(self) -> int:
        return len(self.rows)


class EvidenceResult(BaseModel, frozen=True, extra="forbid"):
    evidence_id: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{1,127}$")
    columns: tuple[str, ...]
    rows: tuple[dict[str, QueryScalar], ...]
    query_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_shape(self) -> "EvidenceResult":
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("evidence columns must be unique")
        expected = set(self.columns)
        if any(set(row) != expected for row in self.rows):
            raise ValueError("evidence rows must match declared columns")
        return self


class TotalRule(BaseModel, frozen=True, extra="forbid"):
    type: Literal["total"]
    parent_ref: str
    child_ref: str
    column: str


class GrainRule(BaseModel, frozen=True, extra="forbid"):
    type: Literal["grain"]
    evidence_ref: str
    key_columns: tuple[str, ...] = Field(min_length=1, max_length=8)


class SetRule(BaseModel, frozen=True, extra="forbid"):
    type: Literal["set"]
    left_ref: str
    right_ref: str
    columns: tuple[str, ...] = Field(min_length=1, max_length=8)


class NullRule(BaseModel, frozen=True, extra="forbid"):
    type: Literal["null"]
    evidence_ref: str
    columns: tuple[str, ...] = Field(min_length=1, max_length=32)
    allow_null: bool = False


class DecimalToleranceRule(BaseModel, frozen=True, extra="forbid"):
    type: Literal["decimal_tolerance"]
    left_ref: str
    right_ref: str
    column: str
    absolute_tolerance: Decimal = Field(ge=0, allow_inf_nan=False, decimal_places=6)


ReconciliationRule = Annotated[
    TotalRule | GrainRule | SetRule | NullRule | DecimalToleranceRule,
    Field(discriminator="type"),
]


class ReconciliationRequest(BaseModel, frozen=True, extra="forbid"):
    evidence: tuple[EvidenceResult, ...] = Field(min_length=1, max_length=32)
    rules: tuple[ReconciliationRule, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_unique_evidence(self) -> "ReconciliationRequest":
        identities = tuple(item.evidence_id for item in self.evidence)
        if len(set(identities)) != len(identities):
            raise ValueError("evidence IDs must be unique")
        return self


class ReconciliationCheck(BaseModel, frozen=True, extra="forbid"):
    rule_type: Literal["total", "grain", "set", "null", "decimal_tolerance"]
    passed: bool
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    actual_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReconciliationResult(BaseModel, frozen=True, extra="forbid"):
    passed: bool
    checks: tuple[ReconciliationCheck, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_summary(self) -> "ReconciliationResult":
        if self.passed != all(item.passed for item in self.checks):
            raise ValueError("reconciliation summary must match checks")
        return self
