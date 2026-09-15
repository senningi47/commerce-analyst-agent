from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from commerce_agent.model.contracts import (
    ChatMessage,
    CostEstimate,
    CostUnavailable,
    FinalOutput,
    ModelAttemptSummary,
    ModelRequest,
    ModelResponse,
    NonThinkingConfig,
    PendingToolBatch,
    ProviderTurnRef,
    ReportedUsage,
    RunScope,
    ThinkingConfig,
    ToolCall,
    ToolCallOutput,
    ToolDefinition,
    ToolExchangeGroup,
    ToolResult,
    UsageUnavailable,
)
from commerce_agent.model.errors import (
    ModelAuthError,
    ModelBalanceError,
    ModelCapabilityError,
    ModelConfigurationError,
    ModelProtocolError,
    ModelRateLimited,
    ModelStateError,
    ModelTransportError,
)
from tests.contracts.model_gateway import assert_model_response_contract

RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
ATTEMPT_ID = UUID("00000000-0000-0000-0000-000000000002")
TURN_ID = UUID("00000000-0000-0000-0000-000000000003")


def provider_ref(expected: tuple[str, ...]) -> ProviderTurnRef:
    return ProviderTurnRef(
        turn_id=TURN_ID,
        scope_digest="b" * 64,
        attempt_id=ATTEMPT_ID,
        sequence=0,
        payload_sha256="c" * 64,
        expected_tool_call_ids=expected,
        token_weight=12,
        expires_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


def valid_model_request() -> dict[str, object]:
    return {
        "run_scope": RunScope(
            run_id=RUN_ID,
            track="retail",
            mode="retail",
            subject_id="retail-demo",
            experiment_id="day3",
            config_hash="a" * 64,
        ),
        "attempt_id": ATTEMPT_ID,
        "sequence": 0,
        "inference": NonThinkingConfig(
            type="disabled",
            temperature=0,
            max_output_tokens=4096,
        ),
        "messages": (ChatMessage(role="user", content="hello"),),
        "history": (),
        "tools": (),
        "timeout_seconds": "30",
        "capability_revision": "deepseek-v4-flash-capability-v3",
        "provider_user_id": "d" * 32,
        "prompt_policy_hash": "1" * 64,
        "rendered_prompt_hash": "2" * 64,
        "tool_hash": "3" * 64,
        "context_hash": "4" * 64,
        "config_hash": "a" * 64,
    }


def pending_batch() -> PendingToolBatch:
    call = ToolCall(call_id="call_1", name="execute_readonly_sql", arguments_json="{}")
    return PendingToolBatch(
        provider_turn_ref=provider_ref(expected=("call_1",)),
        tool_calls=(call,),
        expected_tool_call_ids=("call_1",),
        graph_node_revision="retail-nodes-v1",
        budget_snapshot_json="{}",
        prompt_policy_hash="1" * 64,
        rendered_prompt_hash="2" * 64,
        tool_hash="3" * 64,
        context_hash="4" * 64,
        config_hash="a" * 64,
    )


def test_run_scope_rejects_cross_track_modes() -> None:
    assert (
        RunScope(
            run_id=RUN_ID,
            track="retail",
            mode="retail",
            subject_id="retail-demo",
            experiment_id="day3",
            config_hash="a" * 64,
        ).mode
        == "retail"
    )
    with pytest.raises(ValidationError, match="track and mode"):
        RunScope(
            run_id=RUN_ID,
            track="bird",
            mode="retail",
            subject_id="synthetic",
            experiment_id="day3",
            config_hash="a" * 64,
        )


def test_thinking_and_non_thinking_are_disjoint() -> None:
    assert ThinkingConfig(type="enabled", effort="high", max_output_tokens=8192)
    assert NonThinkingConfig(type="disabled", temperature=0, max_output_tokens=4096)
    with pytest.raises(ValidationError):
        ThinkingConfig(
            type="enabled",
            effort="high",
            max_output_tokens=8192,
            temperature=0,
        )


def test_reported_usage_enforces_provider_identities() -> None:
    usage = ReportedUsage(
        status="reported",
        prompt_tokens=10,
        cache_hit_tokens=4,
        cache_miss_tokens=6,
        completion_tokens=8,
        reasoning_tokens=3,
        total_tokens=18,
    )
    assert usage.total_tokens == 18
    with pytest.raises(ValidationError, match="cache"):
        ReportedUsage.model_validate(usage.model_dump() | {"cache_miss_tokens": 5})


def test_chat_messages_accept_only_public_prompt_roles() -> None:
    message = ChatMessage(role="user", content="hello")
    with pytest.raises(ValidationError):
        message.content = "changed"
    with pytest.raises(ValidationError):
        ChatMessage(role="assistant", content="private provider turn")


def test_tool_exchange_requires_exactly_one_result_per_call() -> None:
    call = ToolCall(call_id="call_1", name="execute_readonly_sql", arguments_json="{}")
    ref = provider_ref(expected=("call_1",))
    with pytest.raises(ValidationError, match="tool result ids"):
        ToolExchangeGroup(
            group_type="tool_exchange",
            provider_turn_ref=ref,
            tool_calls=(call,),
            tool_results=(),
        )


def test_tool_exchange_cannot_be_empty() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        ToolExchangeGroup(
            group_type="tool_exchange",
            provider_turn_ref=provider_ref(expected=()),
            tool_calls=(),
            tool_results=(),
        )


def test_provider_turn_ref_requires_aware_expiry_and_unique_expected_calls() -> None:
    ref = provider_ref(expected=("call_1",))
    with pytest.raises(ValidationError, match="timezone-aware"):
        ProviderTurnRef.model_validate(
            ref.model_dump() | {"expires_at": datetime(2026, 9, 8, tzinfo=UTC).replace(tzinfo=None)}
        )
    with pytest.raises(ValidationError, match="unique"):
        ProviderTurnRef.model_validate(
            ref.model_dump() | {"expected_tool_call_ids": ("call_1", "call_1")}
        )


def test_provider_ref_and_public_response_forbid_private_payload_fields() -> None:
    forbidden = {"reasoning_content", "provider_payload", "assistant_message"}
    assert forbidden.isdisjoint(ProviderTurnRef.model_fields)
    assert forbidden.isdisjoint(ModelResponse.model_fields)
    assert ProviderTurnRef.model_config.get("extra") == "forbid"
    assert ModelResponse.model_config.get("extra") == "forbid"


def test_pending_batch_cannot_be_used_as_a_conversation_group() -> None:
    assert "pending_tool_batch" not in ModelRequest.model_fields
    with pytest.raises(ValidationError):
        ModelRequest.model_validate(valid_model_request() | {"history": [pending_batch()]})


def test_pending_batch_requires_canonical_budget_snapshot() -> None:
    batch = pending_batch()
    with pytest.raises(ValidationError, match="canonical JSON"):
        PendingToolBatch.model_validate(
            batch.model_dump() | {"budget_snapshot_json": '{ "model_calls": 1 }'}
        )


def test_tool_definition_requires_canonical_json_schema() -> None:
    valid = {
        "name": "execute_readonly_sql",
        "description": "Execute one reviewed read-only SQL statement.",
        "catalog_revision": "retail-tools-v1",
        "capability_sha256": "e" * 64,
    }
    assert ToolDefinition(parameters_json="{}", **valid).parameters_json == "{}"
    with pytest.raises(ValidationError, match="canonical JSON"):
        ToolDefinition(parameters_json='{ "type": "object" }', **valid)


def test_tool_call_requires_canonical_arguments_json() -> None:
    with pytest.raises(ValidationError, match="canonical JSON"):
        ToolCall(
            call_id="call_1",
            name="execute_readonly_sql",
            arguments_json='{ "sql": "SELECT 1" }',
        )


def test_tool_result_requires_canonical_public_content() -> None:
    with pytest.raises(ValidationError, match="canonical JSON"):
        ToolResult(
            call_id="call_1",
            name="execute_readonly_sql",
            status="success",
            content_json='{ "rows": [] }',
            deterministic_summary="0 rows",
            content_sha256="a" * 64,
            source_refs=(),
            error_class=None,
            content_mode="raw",
        )


@pytest.mark.parametrize(
    ("status", "error_class"),
    [("success", "QueryError"), ("error", None)],
)
def test_tool_result_error_class_matches_status(
    status: str,
    error_class: str | None,
) -> None:
    with pytest.raises(ValidationError, match="error_class"):
        ToolResult(
            call_id="call_1",
            name="execute_readonly_sql",
            status=status,
            content_json="{}",
            deterministic_summary="safe summary",
            content_sha256="a" * 64,
            source_refs=(),
            error_class=error_class,
            content_mode="raw",
        )


def test_model_outputs_have_closed_discriminated_shapes() -> None:
    assert FinalOutput(type="final", content="done").content == "done"
    call = ToolCall(call_id="call_1", name="execute_readonly_sql", arguments_json="{}")
    assert ToolCallOutput(type="tool_calls", tool_calls=(call,)).tool_calls == (call,)
    with pytest.raises(ValidationError):
        ToolCallOutput(type="tool_calls", tool_calls=())


def test_tool_call_output_rejects_duplicate_call_ids() -> None:
    call = ToolCall(call_id="call_1", name="execute_readonly_sql", arguments_json="{}")
    with pytest.raises(ValidationError, match="unique"):
        ToolCallOutput(type="tool_calls", tool_calls=(call, call))


def test_usage_and_cost_unavailability_are_explicit() -> None:
    usage = UsageUnavailable(status="unavailable", reason_code="provider_usage_missing")
    cost = CostUnavailable(status="unavailable", reason_code="usage_unavailable")
    estimate = CostEstimate(
        status="estimated",
        price_snapshot_id="deepseek-price-v1",
        currency="USD",
        amount=Decimal("0.00042"),
        price_band="off_peak",
    )
    assert usage.reason_code == "provider_usage_missing"
    assert cost.reason_code == "usage_unavailable"
    assert estimate.amount == Decimal("0.00042")


def test_attempt_summary_requires_bounded_attempt_and_aware_times() -> None:
    summary = ModelAttemptSummary(
        attempt_number=1,
        sent_at=datetime(2026, 9, 4, 1, 0, tzinfo=UTC),
        completed_at=datetime(2026, 9, 4, 1, 0, 1, tzinfo=UTC),
        outcome="success",
        http_status=200,
        retryable=False,
        charge_ambiguous=False,
        usage=UsageUnavailable(status="unavailable", reason_code="provider_usage_missing"),
        cost=CostUnavailable(status="unavailable", reason_code="usage_unavailable"),
    )
    assert summary.attempt_number == 1
    with pytest.raises(ValidationError, match="timezone-aware"):
        ModelAttemptSummary.model_validate(
            summary.model_dump()
            | {"completed_at": datetime(2026, 9, 4, 1, 0, 1, tzinfo=UTC).replace(tzinfo=None)}
        )


def test_model_response_exposes_only_the_approved_public_result() -> None:
    usage = UsageUnavailable(status="unavailable", reason_code="provider_usage_missing")
    cost = CostUnavailable(status="unavailable", reason_code="usage_unavailable")
    attempt = ModelAttemptSummary(
        attempt_number=1,
        sent_at=datetime(2026, 9, 4, 1, 0, tzinfo=UTC),
        completed_at=datetime(2026, 9, 4, 1, 0, 1, tzinfo=UTC),
        outcome="success",
        http_status=200,
        retryable=False,
        charge_ambiguous=False,
        usage=usage,
        cost=cost,
    )
    response = ModelResponse(
        output=FinalOutput(type="final", content="done"),
        provider_turn_ref=provider_ref(expected=()),
        finish_reason="stop",
        actual_model="deepseek-v4-flash",
        system_fingerprint=None,
        usage=usage,
        cost=cost,
        attempts=(attempt,),
    )
    assert response.output.content == "done"
    assert set(ModelResponse.model_fields) == {
        "output",
        "provider_turn_ref",
        "finish_reason",
        "actual_model",
        "system_fingerprint",
        "usage",
        "cost",
        "attempts",
    }
    assert_model_response_contract(response)


def test_model_response_binds_output_calls_to_the_private_turn_reference() -> None:
    usage = UsageUnavailable(status="unavailable", reason_code="provider_usage_missing")
    cost = CostUnavailable(status="unavailable", reason_code="usage_unavailable")
    attempt = ModelAttemptSummary(
        attempt_number=1,
        sent_at=datetime(2026, 9, 4, 1, 0, tzinfo=UTC),
        completed_at=datetime(2026, 9, 4, 1, 0, 1, tzinfo=UTC),
        outcome="success",
        http_status=200,
        retryable=False,
        charge_ambiguous=False,
        usage=usage,
        cost=cost,
    )
    call = ToolCall(call_id="call_1", name="execute_readonly_sql", arguments_json="{}")
    with pytest.raises(ValidationError, match="private turn reference"):
        ModelResponse(
            output=ToolCallOutput(type="tool_calls", tool_calls=(call,)),
            provider_turn_ref=provider_ref(expected=("different_call",)),
            finish_reason="tool_calls",
            actual_model="deepseek-v4-flash",
            system_fingerprint=None,
            usage=usage,
            cost=cost,
            attempts=(attempt,),
        )


def test_model_response_rejects_finish_reason_output_mismatch() -> None:
    usage = UsageUnavailable(status="unavailable", reason_code="provider_usage_missing")
    cost = CostUnavailable(status="unavailable", reason_code="usage_unavailable")
    attempt = ModelAttemptSummary(
        attempt_number=1,
        sent_at=datetime(2026, 9, 4, 1, 0, tzinfo=UTC),
        completed_at=datetime(2026, 9, 4, 1, 0, 1, tzinfo=UTC),
        outcome="success",
        http_status=200,
        retryable=False,
        charge_ambiguous=False,
        usage=usage,
        cost=cost,
    )
    with pytest.raises(ValidationError, match="finish reason"):
        ModelResponse(
            output=FinalOutput(type="final", content="done"),
            provider_turn_ref=provider_ref(expected=()),
            finish_reason="tool_calls",
            actual_model="deepseek-v4-flash",
            system_fingerprint=None,
            usage=usage,
            cost=cost,
            attempts=(attempt,),
        )


def test_final_response_cannot_reference_unresolved_tool_calls() -> None:
    usage = UsageUnavailable(status="unavailable", reason_code="provider_usage_missing")
    cost = CostUnavailable(status="unavailable", reason_code="usage_unavailable")
    attempt = ModelAttemptSummary(
        attempt_number=1,
        sent_at=datetime(2026, 9, 4, 1, 0, tzinfo=UTC),
        completed_at=datetime(2026, 9, 4, 1, 0, 1, tzinfo=UTC),
        outcome="success",
        http_status=200,
        retryable=False,
        charge_ambiguous=False,
        usage=usage,
        cost=cost,
    )
    with pytest.raises(ValidationError, match="cannot reference tool calls"):
        ModelResponse(
            output=FinalOutput(type="final", content="done"),
            provider_turn_ref=provider_ref(expected=("call_1",)),
            finish_reason="stop",
            actual_model="deepseek-v4-flash",
            system_fingerprint=None,
            usage=usage,
            cost=cost,
            attempts=(attempt,),
        )


def test_model_response_attempts_start_at_one_and_are_contiguous() -> None:
    usage = UsageUnavailable(status="unavailable", reason_code="provider_usage_missing")
    cost = CostUnavailable(status="unavailable", reason_code="usage_unavailable")
    attempt = ModelAttemptSummary(
        attempt_number=2,
        sent_at=datetime(2026, 9, 4, 1, 0, tzinfo=UTC),
        completed_at=datetime(2026, 9, 4, 1, 0, 1, tzinfo=UTC),
        outcome="success",
        http_status=200,
        retryable=False,
        charge_ambiguous=False,
        usage=usage,
        cost=cost,
    )
    with pytest.raises(ValidationError, match="contiguous"):
        ModelResponse(
            output=FinalOutput(type="final", content="done"),
            provider_turn_ref=provider_ref(expected=()),
            finish_reason="stop",
            actual_model="deepseek-v4-flash",
            system_fingerprint=None,
            usage=usage,
            cost=cost,
            attempts=(attempt,),
        )


def test_model_response_requires_a_successful_final_attempt() -> None:
    usage = UsageUnavailable(status="unavailable", reason_code="provider_usage_missing")
    cost = CostUnavailable(status="unavailable", reason_code="usage_unavailable")
    attempt = ModelAttemptSummary(
        attempt_number=1,
        sent_at=datetime(2026, 9, 4, 1, 0, tzinfo=UTC),
        completed_at=datetime(2026, 9, 4, 1, 0, 1, tzinfo=UTC),
        outcome="http_error",
        http_status=503,
        retryable=True,
        charge_ambiguous=False,
        usage=usage,
        cost=cost,
    )
    with pytest.raises(ValidationError, match="successful final attempt"):
        ModelResponse(
            output=FinalOutput(type="final", content="done"),
            provider_turn_ref=provider_ref(expected=()),
            finish_reason="stop",
            actual_model="deepseek-v4-flash",
            system_fingerprint=None,
            usage=usage,
            cost=cost,
            attempts=(attempt,),
        )


def test_model_response_usage_and_cost_match_the_final_attempt() -> None:
    usage = UsageUnavailable(status="unavailable", reason_code="provider_usage_missing")
    cost = CostUnavailable(status="unavailable", reason_code="usage_unavailable")
    attempt = ModelAttemptSummary(
        attempt_number=1,
        sent_at=datetime(2026, 9, 4, 1, 0, tzinfo=UTC),
        completed_at=datetime(2026, 9, 4, 1, 0, 1, tzinfo=UTC),
        outcome="success",
        http_status=200,
        retryable=False,
        charge_ambiguous=False,
        usage=usage,
        cost=cost,
    )
    with pytest.raises(ValidationError, match="final attempt"):
        ModelResponse(
            output=FinalOutput(type="final", content="done"),
            provider_turn_ref=provider_ref(expected=()),
            finish_reason="stop",
            actual_model="deepseek-v4-flash",
            system_fingerprint=None,
            usage=UsageUnavailable(status="unavailable", reason_code="different_reason"),
            cost=cost,
            attempts=(attempt,),
        )


def test_model_error_taxonomy_exposes_stable_flags_without_sensitive_text() -> None:
    error_types = (
        ModelConfigurationError,
        ModelCapabilityError,
        ModelStateError,
        ModelAuthError,
        ModelBalanceError,
        ModelRateLimited,
        ModelTransportError,
        ModelProtocolError,
    )
    secret = "sk-private-sentinel"
    dsn = "postgresql://writer:private@127.0.0.1:5432/database"
    for error_type in error_types:
        error = error_type(
            "stable_reason",
            f"provider failed with {secret} and {dsn}",
            retryable=False,
            charge_ambiguous=True,
        )
        assert error.reason_code == "stable_reason"
        assert error.retryable is False
        assert error.charge_ambiguous is True
        assert error.attempts == ()
        assert secret not in str(error)
        assert secret not in repr(error)
        assert dsn not in str(error)
        assert dsn not in repr(error)


def test_model_errors_redact_private_reasoning_sentinels() -> None:
    sentinel = "PRIVATE_REASONING_SENTINEL"
    error = ModelProtocolError(
        "provider_payload_invalid",
        sentinel,
        retryable=False,
    )
    assert sentinel not in str(error)
    assert sentinel not in repr(error)


def test_thinking_config_allows_output_headroom_for_long_reasoning() -> None:
    """2026-09-15 verdicts: at 8192 AND at 16384 a reasoning-only turn
    returned finish_reason=length with empty content, and
    `FinalOutput(content='')` failed validation — killing the whole episode.
    The waterline is the provider thinking-mode default output (64K, rename
    probe doc evidence); the cap must sit at or above it."""
    assert ThinkingConfig(type="enabled", effort="high", max_output_tokens=65536)
    with pytest.raises(ValidationError):
        ThinkingConfig(type="enabled", effort="high", max_output_tokens=65537)
