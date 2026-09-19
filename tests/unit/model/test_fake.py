from datetime import UTC, datetime
from uuid import UUID

import pytest

from commerce_agent.model.contracts import (
    ChatMessage,
    CostUnavailable,
    FinalOutput,
    ModelAttemptSummary,
    ModelRequest,
    ModelResponse,
    NonThinkingConfig,
    ProviderTurnRef,
    ReportedUsage,
    RunScope,
)
from commerce_agent.model.errors import ModelTransportError
from commerce_agent.model.fake import FakeModel, FakeModelExhausted

RUN_ID = UUID("00000000-0000-0000-0000-000000000101")
ATTEMPT_ID = UUID("00000000-0000-0000-0000-000000000102")


def model_request(*, sequence: int) -> ModelRequest:
    return ModelRequest(
        run_scope=RunScope(
            run_id=RUN_ID,
            track="retail",
            mode="retail",
            subject_id="retail-demo",
            experiment_id="day3",
            config_hash="a" * 64,
        ),
        attempt_id=ATTEMPT_ID,
        sequence=sequence,
        inference=NonThinkingConfig(
            type="disabled",
            temperature=0,
            max_output_tokens=4096,
        ),
        messages=(ChatMessage(role="user", content="hello"),),
        history=(),
        tools=(),
        timeout_seconds="30",
        capability_revision="deepseek-v4-flash-capability-v3",
        provider_user_id="b" * 32,
        prompt_policy_hash="1" * 64,
        rendered_prompt_hash="2" * 64,
        tool_hash="3" * 64,
        context_hash="4" * 64,
        config_hash="a" * 64,
    )


def final_response(content: str) -> ModelResponse:
    usage = ReportedUsage(
        status="reported",
        prompt_tokens=10,
        cache_hit_tokens=4,
        cache_miss_tokens=6,
        completion_tokens=2,
        reasoning_tokens=0,
        total_tokens=12,
    )
    cost = CostUnavailable(status="unavailable", reason_code="fixture_price_unavailable")
    attempt = ModelAttemptSummary(
        attempt_number=1,
        sent_at=datetime(2026, 9, 4, 2, 0, tzinfo=UTC),
        completed_at=datetime(2026, 9, 4, 2, 0, 1, tzinfo=UTC),
        outcome="success",
        http_status=200,
        retryable=False,
        charge_ambiguous=False,
        usage=usage,
        cost=cost,
    )
    return ModelResponse(
        output=FinalOutput(type="final", content=content),
        provider_turn_ref=ProviderTurnRef(
            turn_id=UUID("00000000-0000-0000-0000-000000000103"),
            scope_digest="c" * 64,
            attempt_id=ATTEMPT_ID,
            sequence=0,
            payload_sha256="d" * 64,
            expected_tool_call_ids=(),
            token_weight=2,
            expires_at=datetime(2026, 9, 11, 2, 0, tzinfo=UTC),
        ),
        finish_reason="stop",
        actual_model="fake",
        system_fingerprint="fake-v1",
        usage=usage,
        cost=cost,
        attempts=(attempt,),
    )


@pytest.mark.asyncio
async def test_fake_records_requests_and_scripts_response_then_error() -> None:
    response = final_response("first")
    error = ModelTransportError(
        "fake_transport",
        "scripted transport failure",
        retryable=True,
    )
    gateway = FakeModel([response, error])
    first_request = model_request(sequence=0)
    second_request = model_request(sequence=1)

    assert await gateway.complete(first_request) == response
    with pytest.raises(ModelTransportError, match="scripted transport"):
        await gateway.complete(second_request)
    assert gateway.requests == (first_request, second_request)


@pytest.mark.asyncio
async def test_fake_exhaustion_is_explicit_and_records_the_request() -> None:
    gateway = FakeModel([])
    request = model_request(sequence=0)

    with pytest.raises(FakeModelExhausted) as caught:
        await gateway.complete(request)
    assert caught.value.reason_code == "fake_model_exhausted"
    assert caught.value.retryable is False
    assert gateway.requests == (request,)
