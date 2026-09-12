"""Closed contracts for profile-selected context assembly."""

import re
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from commerce_agent.model.contracts import (
    ConversationGroup,
    InferenceConfig,
    ModelRequest,
    RunScope,
)


class DatumKind(StrEnum):
    USER_INPUT = "user_input"
    CONFIRMED_FACT = "confirmed_fact"
    SCHEMA = "schema"
    KNOWLEDGE = "knowledge"
    BUSINESS_VALUE = "business_value"
    TOOL_RESULT = "tool_result"
    OFFICIAL_FEEDBACK = "official_feedback"
    PHASE = "phase"
    ERROR = "error"


class DataNamespace(StrEnum):
    USER_INPUT = "user_input"
    CONFIRMED_FACT = "confirmed_fact"
    RUNTIME_ERROR = "runtime_error"
    OLIST_SCHEMA = "olist_schema"
    RETAIL_KNOWLEDGE = "retail_knowledge"
    BUSINESS_VALUE = "business_value"
    PRODUCT_QUERY_RESULT = "product_query_result"
    BIRD_SCHEMA = "bird_schema"
    BIRD_OFFICIAL_FEEDBACK = "bird_official_feedback"
    BIRD_A_TOOL_RESULT = "bird_a_tool_result"
    BIRD_C_PHASE = "bird_c_phase"


class PromptStep(StrEnum):
    RETAIL_DECIDE = "retail_decide"
    RETAIL_CLARIFY = "retail_clarify"
    RETAIL_REPORT = "retail_report"
    RETAIL_SQL_GENERATE = "retail_sql_generate"
    RETAIL_SQL_REPAIR = "retail_sql_repair"
    BIRD_A_ACT = "bird_a_act"
    BIRD_C_RESPOND = "bird_c_respond"


class RunProfileKey(StrEnum):
    RETAIL = "retail"
    BIRD_A = "bird_a"
    BIRD_C = "bird_c"


class ContextDatum(BaseModel, frozen=True, extra="forbid"):
    kind: DatumKind
    namespace: DataNamespace
    source_ref: str = Field(min_length=1, max_length=512)
    revision: str | None = Field(default=None, min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=262_144)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_digest(self) -> "ContextDatum":
        expected = sha256(self.content.encode("utf-8")).hexdigest()
        if self.digest != expected:
            raise ValueError("context datum digest does not match content")
        return self


_SENSITIVE_ERROR = re.compile(
    r"(?:\b(?:postgres(?:ql)?|mysql|redis)://|\bsk-[A-Za-z0-9_-]+|"
    r"\b(?:api[_ -]?key|authorization|bearer|dsn|password|secret)\b|[{}\[\]]|\r|\n)",
    flags=re.IGNORECASE,
)


class TypedErrorDatum(BaseModel, frozen=True, extra="forbid"):
    error_type: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{1,127}$")
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    retryable: bool
    message: str = Field(min_length=1, max_length=256)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_safe_message_and_digest(self) -> "TypedErrorDatum":
        if _SENSITIVE_ERROR.search(self.message):
            raise ValueError("error message must be sanitized")
        if self.digest != sha256(self.message.encode("utf-8")).hexdigest():
            raise ValueError("error datum digest does not match message")
        return self


class InferenceRule(BaseModel, frozen=True, extra="forbid"):
    step: PromptStep
    inference: InferenceConfig
    tool_names: tuple[str, ...]

    @model_validator(mode="after")
    def validate_tool_names(self) -> "InferenceRule":
        if len(set(self.tool_names)) != len(self.tool_names):
            raise ValueError("step tool names must be unique")
        return self


class StopReasonRule(BaseModel, frozen=True, extra="forbid"):
    trigger: Literal[
        "model_budget",
        "tool_budget",
        "repeat_digest",
        "unsafe_finish",
        "incomplete_finish",
        "exhausted_infrastructure",
    ]
    stop_kind: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")


class _RunProfileBase(BaseModel, frozen=True, extra="forbid"):
    key: RunProfileKey
    revision: str = Field(min_length=1, max_length=128)
    prompt_policy_revision: str = Field(min_length=1, max_length=128)
    tool_catalog_revision: str = Field(min_length=1, max_length=128)
    allowed_namespaces: tuple[DataNamespace, ...]
    inference_rules: tuple[InferenceRule, ...]
    input_token_limit: int = Field(default=65_536, ge=1, le=65_536)
    tool_result_max_bytes: int = Field(default=65_536, ge=1, le=65_536)
    minimum_recent_groups: int = Field(default=2, ge=0)
    stop_reason_rules: tuple[StopReasonRule, ...]
    capability_revision: str = Field(min_length=1, max_length=128)
    estimator_revision: str = Field(min_length=1, max_length=128)
    canonicalization_revision: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_unique_rules_and_namespaces(self) -> "_RunProfileBase":
        if len(set(self.allowed_namespaces)) != len(self.allowed_namespaces):
            raise ValueError("allowed namespaces must be unique")
        steps = tuple(rule.step for rule in self.inference_rules)
        if len(set(steps)) != len(steps):
            raise ValueError("inference steps must be unique")
        triggers = tuple(rule.trigger for rule in self.stop_reason_rules)
        if len(set(triggers)) != len(triggers):
            raise ValueError("stop triggers must be unique")
        return self


class RetailProfile(_RunProfileBase):
    kind: Literal["retail"]
    key: Literal[RunProfileKey.RETAIL]


class BirdAProfile(_RunProfileBase):
    kind: Literal["bird_a"]
    key: Literal[RunProfileKey.BIRD_A]


class BirdCProfile(_RunProfileBase):
    kind: Literal["bird_c"]
    key: Literal[RunProfileKey.BIRD_C]


RunProfile = Annotated[
    RetailProfile | BirdAProfile | BirdCProfile,
    Field(discriminator="kind"),
]


class ContextRequest(BaseModel, frozen=True, extra="forbid"):
    run_scope: RunScope
    attempt_id: UUID
    sequence: int = Field(ge=0)
    profile: RunProfile
    step: PromptStep
    current_input: ContextDatum
    confirmed_facts: tuple[ContextDatum, ...] = ()
    evidence: tuple[ContextDatum, ...] = ()
    latest_error: TypedErrorDatum | None = None
    history: tuple[ConversationGroup, ...] = ()


class TrimmingSummary(BaseModel, frozen=True, extra="forbid"):
    original_groups: int = Field(ge=0)
    retained_groups: int = Field(ge=0)
    removed_group_digests: tuple[str, ...]
    compressed_result_digests: tuple[str, ...]
    removed_evidence_digests: tuple[str, ...]
    estimated_before: int = Field(ge=0)
    estimated_after: int = Field(ge=0)


class PromptBundle(BaseModel, frozen=True, extra="forbid"):
    model_request: ModelRequest
    profile: RunProfileKey
    prompt_policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rendered_prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    estimated_input_tokens: int = Field(ge=0)
    trimming: TrimmingSummary

    @model_validator(mode="after")
    def validate_embedded_request(self) -> "PromptBundle":
        for field_name in (
            "prompt_policy_hash",
            "rendered_prompt_hash",
            "tool_hash",
            "context_hash",
            "config_hash",
        ):
            if getattr(self, field_name) != getattr(self.model_request, field_name):
                raise ValueError(f"{field_name} must match embedded model request")
        if self.estimated_input_tokens != self.trimming.estimated_after:
            raise ValueError("estimated input tokens must match trimming estimate")
        return self
