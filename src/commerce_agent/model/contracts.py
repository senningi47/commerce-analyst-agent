"""Provider-neutral contracts for model adapters."""

import json
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


def _validate_canonical_json(value: str) -> str:
    def reject_constant(token: str) -> None:
        raise ValueError(f"non-finite JSON value {token} is not allowed")

    try:
        parsed = json.loads(value, parse_constant=reject_constant)
        canonical = json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("value must be valid canonical JSON") from error
    if value != canonical:
        raise ValueError("value must use canonical JSON encoding")
    return value


class RunScope(BaseModel, frozen=True, extra="forbid"):
    """Stable, non-secret identity and configuration binding for one run."""

    run_id: UUID
    track: Literal["retail", "bird"]
    mode: Literal["retail", "a", "c"]
    subject_id: str = Field(min_length=1, max_length=128)
    experiment_id: str = Field(min_length=1, max_length=128)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_track_mode(self) -> "RunScope":
        if (self.track, self.mode) not in {
            ("retail", "retail"),
            ("bird", "a"),
            ("bird", "c"),
        }:
            raise ValueError("track and mode must identify the same execution track")
        return self


class ThinkingConfig(BaseModel, frozen=True, extra="forbid"):
    """Inference settings accepted when provider reasoning is enabled."""

    type: Literal["enabled"]
    effort: Literal["low", "high", "max"]
    # 65536 = provider thinking-mode default output (rename-probe doc
    # evidence); reasoning blowouts emptied the content at both 8192 and
    # 16384 — the official ADK stack (no max_tokens override) runs at 64K
    max_output_tokens: int = Field(ge=1, le=65536)


class NonThinkingConfig(BaseModel, frozen=True, extra="forbid"):
    """Inference settings accepted when provider reasoning is disabled."""

    type: Literal["disabled"]
    temperature: float = Field(ge=0, le=2)
    max_output_tokens: int = Field(ge=1, le=4096)


InferenceConfig = Annotated[
    ThinkingConfig | NonThinkingConfig,
    Field(discriminator="type"),
]


class ReportedUsage(BaseModel, frozen=True, extra="forbid"):
    """Provider-reported token counts with verified accounting identities."""

    status: Literal["reported"]
    prompt_tokens: int = Field(ge=0)
    cache_hit_tokens: int = Field(ge=0)
    cache_miss_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_token_identities(self) -> "ReportedUsage":
        if self.prompt_tokens != self.cache_hit_tokens + self.cache_miss_tokens:
            raise ValueError("prompt token count must equal cache hit plus cache miss tokens")
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("total token count must equal prompt plus completion tokens")
        if self.reasoning_tokens > self.completion_tokens:
            raise ValueError("reasoning tokens cannot exceed completion tokens")
        return self


class UsageUnavailable(BaseModel, frozen=True, extra="forbid"):
    """Explicit marker used when the provider did not report token usage."""

    status: Literal["unavailable"]
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")


Usage = Annotated[
    ReportedUsage | UsageUnavailable,
    Field(discriminator="status"),
]


class CostEstimate(BaseModel, frozen=True, extra="forbid"):
    """Cost derived from validated usage and one reviewed price snapshot."""

    status: Literal["estimated"]
    price_snapshot_id: str = Field(min_length=1, max_length=128)
    currency: Literal["USD"]
    amount: Decimal = Field(ge=0, allow_inf_nan=False)
    price_band: Literal["peak", "off_peak"]


class CostUnavailable(BaseModel, frozen=True, extra="forbid"):
    """Explicit marker used when a trustworthy cost cannot be calculated."""

    status: Literal["unavailable"]
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")


Cost = Annotated[
    CostEstimate | CostUnavailable,
    Field(discriminator="status"),
]


class ModelAttemptSummary(BaseModel, frozen=True, extra="forbid"):
    """Sanitized accounting and disposition for one serial provider attempt."""

    attempt_number: int = Field(ge=1, le=3)
    sent_at: datetime
    completed_at: datetime
    outcome: Literal[
        "success",
        "http_error",
        "transport_error",
        "protocol_error",
        "insufficient_system_resource",
    ]
    http_status: int | None = Field(default=None, ge=100, le=599)
    retryable: bool
    charge_ambiguous: bool
    usage: Usage
    cost: Cost

    @model_validator(mode="after")
    def validate_timestamps(self) -> "ModelAttemptSummary":
        if self.sent_at.tzinfo is None or self.sent_at.utcoffset() is None:
            raise ValueError("sent_at must be timezone-aware")
        if self.completed_at.tzinfo is None or self.completed_at.utcoffset() is None:
            raise ValueError("completed_at must be timezone-aware")
        if self.completed_at < self.sent_at:
            raise ValueError("completed_at cannot precede sent_at")
        return self


class ToolCall(BaseModel, frozen=True, extra="forbid"):
    """One provider-neutral request to a registered tool."""

    call_id: str = Field(min_length=1, max_length=128)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    arguments_json: str = Field(min_length=1, max_length=262_144)

    _canonical_arguments = field_validator("arguments_json")(_validate_canonical_json)


class ToolResult(BaseModel, frozen=True, extra="forbid"):
    """Bounded public result paired to one tool call."""

    call_id: str = Field(min_length=1, max_length=128)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    status: Literal["success", "error"]
    content_json: str = Field(min_length=1, max_length=262_144)
    deterministic_summary: str = Field(min_length=1, max_length=16_384)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_refs: tuple[str, ...]
    error_class: str | None = Field(default=None, min_length=1, max_length=128)
    content_mode: Literal["raw", "summary"] = "raw"

    _canonical_content = field_validator("content_json")(_validate_canonical_json)

    @model_validator(mode="after")
    def validate_error_class(self) -> "ToolResult":
        if self.status == "success" and self.error_class is not None:
            raise ValueError("successful tool results cannot carry error_class")
        if self.status == "error" and self.error_class is None:
            raise ValueError("error tool results require error_class")
        return self


class ProviderTurnRef(BaseModel, frozen=True, extra="forbid"):
    """Opaque binding to a provider-private assistant message."""

    turn_id: UUID
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_id: UUID
    sequence: int = Field(ge=0)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_tool_call_ids: tuple[str, ...]
    token_weight: int = Field(ge=0)
    expires_at: datetime

    @model_validator(mode="after")
    def validate_expiry_and_expected_calls(self) -> "ProviderTurnRef":
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        if len(set(self.expected_tool_call_ids)) != len(self.expected_tool_call_ids):
            raise ValueError("expected tool call ids must be unique")
        if any(not call_id for call_id in self.expected_tool_call_ids):
            raise ValueError("expected tool call ids cannot be empty")
        return self


class AssistantTurnGroup(BaseModel, frozen=True, extra="forbid"):
    """One complete assistant turn that requested no tools."""

    group_type: Literal["assistant"]
    provider_turn_ref: ProviderTurnRef

    @model_validator(mode="after")
    def validate_no_expected_calls(self) -> "AssistantTurnGroup":
        if self.provider_turn_ref.expected_tool_call_ids:
            raise ValueError("assistant groups cannot reference expected tool calls")
        return self


class ToolExchangeGroup(BaseModel, frozen=True, extra="forbid"):
    """One complete assistant tool request and its ordered results."""

    group_type: Literal["tool_exchange"]
    provider_turn_ref: ProviderTurnRef
    tool_calls: tuple[ToolCall, ...] = Field(min_length=1, max_length=8)
    tool_results: tuple[ToolResult, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def validate_closed_exchange(self) -> "ToolExchangeGroup":
        call_ids = tuple(call.call_id for call in self.tool_calls)
        result_ids = tuple(result.call_id for result in self.tool_results)
        if result_ids != call_ids:
            raise ValueError("tool result ids must match tool call ids in order")
        if call_ids != self.provider_turn_ref.expected_tool_call_ids:
            raise ValueError("tool call ids must match the provider turn reference")
        if any(
            call.name != result.name for call, result in zip(self.tool_calls, self.tool_results)
        ):
            raise ValueError("tool result names must match tool call names")
        return self


ConversationGroup = Annotated[
    AssistantTurnGroup | ToolExchangeGroup,
    Field(discriminator="group_type"),
]


class PendingToolBatch(BaseModel, frozen=True, extra="forbid"):
    """Public, resumable tool calls that have not formed a complete exchange."""

    provider_turn_ref: ProviderTurnRef
    tool_calls: tuple[ToolCall, ...] = Field(min_length=1, max_length=8)
    expected_tool_call_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    graph_node_revision: str = Field(min_length=1, max_length=128)
    budget_snapshot_json: str = Field(min_length=1, max_length=16_384)
    prompt_policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rendered_prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    _canonical_budget = field_validator("budget_snapshot_json")(_validate_canonical_json)

    @model_validator(mode="after")
    def validate_expected_calls(self) -> "PendingToolBatch":
        call_ids = tuple(call.call_id for call in self.tool_calls)
        if len(set(call_ids)) != len(call_ids):
            raise ValueError("pending tool call ids must be unique")
        if call_ids != self.expected_tool_call_ids:
            raise ValueError("pending expected tool call ids must match tool calls")
        if call_ids != self.provider_turn_ref.expected_tool_call_ids:
            raise ValueError("pending tool calls must match the provider turn reference")
        return self


class ChatMessage(BaseModel, frozen=True, extra="forbid"):
    """One normalized message sent to a model provider."""

    role: Literal["system", "user"]
    content: str = Field(min_length=1, max_length=262_144)


class ToolDefinition(BaseModel, frozen=True, extra="forbid"):
    """One immutable tool declaration selected by a registered profile."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    description: str = Field(min_length=1, max_length=4_000)
    parameters_json: str = Field(min_length=1, max_length=65_536)
    catalog_revision: str = Field(min_length=1, max_length=128)
    capability_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    _canonical_parameters = field_validator("parameters_json")(_validate_canonical_json)


class FinalOutput(BaseModel, frozen=True, extra="forbid"):
    """One complete public text candidate."""

    type: Literal["final"]
    content: str = Field(min_length=1, max_length=262_144)


class ToolCallOutput(BaseModel, frozen=True, extra="forbid"):
    """One bounded batch of public tool calls."""

    type: Literal["tool_calls"]
    tool_calls: tuple[ToolCall, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_unique_call_ids(self) -> "ToolCallOutput":
        call_ids = tuple(call.call_id for call in self.tool_calls)
        if len(set(call_ids)) != len(call_ids):
            raise ValueError("tool call ids must be unique")
        return self


ModelOutput = Annotated[
    FinalOutput | ToolCallOutput,
    Field(discriminator="type"),
]


class ModelRequest(BaseModel, frozen=True, extra="forbid"):
    """The complete provider-neutral input for one model completion."""

    run_scope: RunScope
    attempt_id: UUID
    sequence: int = Field(ge=0)
    inference: InferenceConfig
    messages: tuple[ChatMessage, ...] = Field(min_length=1)
    history: tuple[ConversationGroup, ...] = ()
    tools: tuple[ToolDefinition, ...] = ()
    timeout_seconds: Decimal = Field(gt=0, le=600)
    capability_revision: str = Field(min_length=1, max_length=128)
    provider_user_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    prompt_policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rendered_prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class FinishReason(StrEnum):
    """Provider finish reasons recognized by orchestration."""

    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    INSUFFICIENT_SYSTEM_RESOURCE = "insufficient_system_resource"


class ModelResponse(BaseModel, frozen=True, extra="forbid"):
    """The complete provider-neutral public result of one completion."""

    output: ModelOutput
    provider_turn_ref: ProviderTurnRef
    finish_reason: FinishReason
    actual_model: str = Field(min_length=1, max_length=256)
    system_fingerprint: str | None = Field(default=None, min_length=1, max_length=256)
    usage: Usage
    cost: Cost
    attempts: tuple[ModelAttemptSummary, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_tool_call_binding(self) -> "ModelResponse":
        attempt_numbers = tuple(attempt.attempt_number for attempt in self.attempts)
        if attempt_numbers != tuple(range(1, len(self.attempts) + 1)):
            raise ValueError("model response attempts must be contiguous from one")
        if self.attempts[-1].outcome != "success":
            raise ValueError("model response requires a successful final attempt")
        if self.usage != self.attempts[-1].usage or self.cost != self.attempts[-1].cost:
            raise ValueError("model response usage and cost must match the final attempt")
        if isinstance(self.output, ToolCallOutput):
            if self.finish_reason is not FinishReason.TOOL_CALLS:
                raise ValueError("tool-call output requires the tool_calls finish reason")
            call_ids = tuple(call.call_id for call in self.output.tool_calls)
            if call_ids != self.provider_turn_ref.expected_tool_call_ids:
                raise ValueError("tool calls must match the private turn reference")
        elif self.finish_reason is FinishReason.TOOL_CALLS:
            raise ValueError("the tool_calls finish reason requires tool-call output")
        elif self.provider_turn_ref.expected_tool_call_ids:
            raise ValueError("final output cannot reference tool calls")
        return self


class ModelGateway(Protocol):
    """The only model-completion seam consumed by application workflows."""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Return one normalized response for the supplied model request."""
