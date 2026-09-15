import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from commerce_agent.context_builder._canonical import canonical_json, sha256_canonical
from commerce_agent.context_builder.builder import scope_digest
from commerce_agent.model._pricing import PriceSnapshot
from commerce_agent.model._retry import RetryPolicy
from commerce_agent.model._turn_store import InMemoryProviderTurnStore, PrivateProviderTurn
from commerce_agent.model.contracts import (
    AssistantTurnGroup,
    ChatMessage,
    ModelRequest,
    NonThinkingConfig,
    RunScope,
    ThinkingConfig,
    ToolCall,
    ToolDefinition,
    ToolExchangeGroup,
    ToolResult,
)
from commerce_agent.model.errors import (
    ModelAuthError,
    ModelBalanceError,
    ModelGatewayError,
    ModelProtocolError,
    ModelRateLimited,
    ModelTransportError,
)
from commerce_agent.model.gateway import (
    CapabilitySnapshot,
    DeepSeekModelGateway,
    NoopAttemptGuard,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "model"
NOW = datetime(2026, 9, 5, 2, tzinfo=UTC)
ATTEMPT_ID = UUID("00000000-0000-0000-0000-000000000502")


class Clock:
    def now(self) -> datetime:
        return NOW


class RecordingSleeper:
    def __init__(self) -> None:
        self.delays: list[Decimal] = []

    async def __call__(self, delay: Decimal) -> None:
        self.delays.append(delay)


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: list[httpx.Response | httpx.TransportError]) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, httpx.TransportError):
            raise response
        response.request = request
        return response


def fixture_response(name: str) -> httpx.Response:
    return httpx.Response(200, json=json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def price_snapshot() -> PriceSnapshot:
    return PriceSnapshot(
        snapshot_id="prices-v1",
        currency="USD",
        peak_windows_utc=(("01:00", "04:00"), ("06:00", "10:00")),
        peak_cache_hit_per_million=Decimal("0.014"),
        peak_cache_miss_per_million=Decimal("0.44"),
        peak_output_per_million=Decimal("1.32"),
        off_peak_cache_hit_per_million=Decimal("0.007"),
        off_peak_cache_miss_per_million=Decimal("0.22"),
        off_peak_output_per_million=Decimal("0.66"),
    )


def tool() -> ToolDefinition:
    return ToolDefinition(
        name="execute_readonly_sql",
        description="Execute read-only SQL.",
        parameters_json='{"additionalProperties":false,"properties":{"sql":{"type":"string"}},"required":["sql"],"type":"object"}',
        catalog_revision="retail-tools-v1",
        capability_sha256="a" * 64,
    )


def model_request(*, thinking: bool = True, tools: bool = True) -> ModelRequest:
    scope = RunScope(
        run_id=UUID("00000000-0000-0000-0000-000000000501"),
        track="retail",
        mode="retail",
        subject_id="subject",
        experiment_id="experiment",
        config_hash="b" * 64,
    )
    inference = (
        ThinkingConfig(type="enabled", effort="high", max_output_tokens=8192)
        if thinking
        else NonThinkingConfig(type="disabled", temperature=0, max_output_tokens=4096)
    )
    return ModelRequest(
        run_scope=scope,
        attempt_id=ATTEMPT_ID,
        sequence=0,
        inference=inference,
        messages=(
            ChatMessage(role="system", content="policy"),
            ChatMessage(role="user", content="question"),
        ),
        tools=(tool(),) if tools else (),
        timeout_seconds=Decimal(30),
        capability_revision="capability-v1",
        provider_user_id="c" * 32,
        prompt_policy_hash="d" * 64,
        rendered_prompt_hash="e" * 64,
        tool_hash="f" * 64,
        context_hash="1" * 64,
        config_hash="b" * 64,
    )


def gateway(
    transport: RecordingTransport,
    sleeper: RecordingSleeper | None = None,
    *,
    store=None,  # type: ignore[no-untyped-def]
    guard=None,  # type: ignore[no-untyped-def]
):
    client = httpx.AsyncClient(transport=transport, base_url="https://api.deepseek.com")
    return DeepSeekModelGateway(
        client=client,
        turn_store=store or InMemoryProviderTurnStore(clock=Clock()),
        capability=CapabilitySnapshot(
            revision="capability-v1",
            requested_model="deepseek-v4-flash",
            response_model="deepseek-v4-flash",
            backend_model="DeepSeek-V4-Flash-0731",
            thinking_efforts=("low", "high", "max"),
            thinking_effort_field="reasoning_effort",
            provider_user_id_field="user_id",
            tool_calling=True,
            thinking_tool_choice=False,
            replay_rule="all_assistant_turns_when_tools",
            expected_usage_fields=(
                "prompt_tokens",
                "prompt_cache_hit_tokens",
                "prompt_cache_miss_tokens",
                "completion_tokens",
                "total_tokens",
            ),
            context_token_limit=1_000_000,
            output_token_limit=384_000,
            probed_at=NOW,
            source_urls=("https://api-docs.deepseek.com/",),
            source_content_hashes=("a" * 64,),
        ),
        prices=price_snapshot(),
        retry_policy=RetryPolicy(),
        clock=Clock(),
        sleeper=sleeper or RecordingSleeper(),
        jitter_rng=lambda: Decimal(0),
        attempt_guard=guard,
    ), client


@pytest.mark.asyncio
async def test_thinking_request_omits_invalid_sampling_and_tool_choice() -> None:
    transport = RecordingTransport([fixture_response("final_success.json")])
    model, client = gateway(transport)
    try:
        response = await model.complete(model_request())
    finally:
        await client.aclose()
    body = json.loads(transport.requests[0].content)
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "high"
    assert body["max_tokens"] == 8192
    for field in ("temperature", "top_p", "presence_penalty", "frequency_penalty", "tool_choice"):
        assert field not in body
    assert body["user_id"] == "c" * 32
    assert "user" not in body
    assert "PRIVATE_REASONING_SENTINEL" not in response.model_dump_json()


@pytest.mark.asyncio
async def test_nonthinking_request_sends_temperature_without_effort() -> None:
    transport = RecordingTransport([fixture_response("final_success.json")])
    model, client = gateway(transport)
    try:
        await model.complete(model_request(thinking=False))
    finally:
        await client.aclose()
    body = json.loads(transport.requests[0].content)
    assert body["thinking"] == {"type": "disabled"}
    assert body["temperature"] == 0
    assert "reasoning_effort" not in body


@pytest.mark.asyncio
async def test_gateway_accepts_reviewed_response_model_not_backend_name() -> None:
    payload = json.loads((FIXTURES / "final_success.json").read_text(encoding="utf-8"))
    payload["model"] = "deepseek-v4-flash"
    transport = RecordingTransport([httpx.Response(200, json=payload)])
    model, client = gateway(transport)
    try:
        response = await model.complete(model_request())
    finally:
        await client.aclose()

    assert response.actual_model == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_retryable_status_is_serial_and_stops_at_success() -> None:
    sleeper = RecordingSleeper()
    transport = RecordingTransport(
        [httpx.Response(503, headers={"Retry-After": "40"}), fixture_response("final_success.json")]
    )
    model, client = gateway(transport, sleeper)
    try:
        response = await model.complete(model_request())
    finally:
        await client.aclose()
    assert len(transport.requests) == 2
    assert sleeper.delays == [Decimal(30)]
    assert [attempt.attempt_number for attempt in response.attempts] == [1, 2]


@pytest.mark.asyncio
async def test_terminal_and_malformed_responses_are_not_retried() -> None:
    for supplied, error_type in (
        (httpx.Response(400), ModelGatewayError),
        (fixture_response("malformed_usage.json"), ModelProtocolError),
    ):
        transport = RecordingTransport([supplied])
        model, client = gateway(transport)
        try:
            with pytest.raises(error_type):
                await model.complete(model_request())
        finally:
            await client.aclose()
        assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_tool_call_is_public_but_reasoning_stays_private() -> None:
    transport = RecordingTransport([fixture_response("tool_call_success.json")])
    model, client = gateway(transport)
    try:
        response = await model.complete(model_request())
    finally:
        await client.aclose()
    assert response.finish_reason == "tool_calls"
    assert response.output.tool_calls[0].arguments_json == '{"sql":"SELECT 1"}'
    assert response.provider_turn_ref.expected_tool_call_ids == ("call_1",)
    assert "PRIVATE_TOOL_REASONING" not in response.model_dump_json()


@pytest.mark.asyncio
async def test_missing_usage_is_explicitly_unavailable() -> None:
    payload = json.loads((FIXTURES / "final_success.json").read_text(encoding="utf-8"))
    payload.pop("usage")
    transport = RecordingTransport([httpx.Response(200, json=payload)])
    model, client = gateway(transport)
    try:
        response = await model.complete(model_request())
    finally:
        await client.aclose()
    assert response.usage.status == "unavailable"
    assert response.cost.status == "unavailable"


class RecordingGuard(NoopAttemptGuard):
    def __init__(self, *, reject: bool = False) -> None:
        self.reject = reject
        self.events: list[tuple[str, int]] = []

    async def before_send(self, request, attempt_number, sent_at):  # type: ignore[no-untyped-def]
        self.events.append(("before", attempt_number))
        if self.reject:
            raise RuntimeError("cost guard rejected")

    async def after_attempt(self, request, summary):  # type: ignore[no-untyped-def]
        self.events.append(("after", summary.attempt_number))


@pytest.mark.asyncio
async def test_attempt_guard_wraps_only_actual_http_attempts() -> None:
    guard = RecordingGuard()
    transport = RecordingTransport([httpx.Response(503), fixture_response("final_success.json")])
    model, client = gateway(transport, guard=guard)
    try:
        await model.complete(model_request())
    finally:
        await client.aclose()
    assert guard.events == [("before", 1), ("after", 1), ("before", 2), ("after", 2)]

    rejecting = RecordingGuard(reject=True)
    transport = RecordingTransport([fixture_response("final_success.json")])
    model, client = gateway(transport, guard=rejecting)
    try:
        with pytest.raises(RuntimeError, match="cost guard"):
            await model.complete(model_request())
    finally:
        await client.aclose()
    assert transport.requests == []
    assert rejecting.events == [("before", 1)]


@pytest.mark.asyncio
async def test_every_private_assistant_turn_is_replayed_in_order() -> None:
    store = InMemoryProviderTurnStore(clock=Clock())
    request = model_request()
    digest = scope_digest(request.run_scope)

    async def save_turn(sequence: int, calls: list[dict[str, object]]):
        private_payload = {
            "content": None if calls else f"assistant {sequence}",
            "reasoning_content": f"private {sequence}",
            "tool_calls": calls,
        }
        return await store.save(
            PrivateProviderTurn(
                scope_digest=digest,
                attempt_id=ATTEMPT_ID,
                sequence=sequence,
                provider="deepseek",
                model="deepseek-v4-flash",
                content=private_payload["content"],
                reasoning_content=private_payload["reasoning_content"],
                tool_calls_json=canonical_json(calls),
                payload_sha256=sha256_canonical(private_payload),
                expected_tool_call_ids=tuple(str(call["id"]) for call in calls),
                token_weight=10,
                created_at=NOW,
                last_used_at=NOW,
                absolute_expires_at=NOW.replace(year=2026, month=9, day=12),
            )
        )

    first_ref = await save_turn(0, [])
    provider_call = {
        "function": {"arguments": '{"sql":"SELECT 1"}', "name": "execute_readonly_sql"},
        "id": "call_1",
        "type": "function",
    }
    second_ref = await save_turn(1, [provider_call])
    call = ToolCall(
        call_id="call_1",
        name="execute_readonly_sql",
        arguments_json='{"sql":"SELECT 1"}',
    )
    content = '{"rows":[1]}'
    result = ToolResult(
        call_id="call_1",
        name=call.name,
        status="success",
        content_json=content,
        deterministic_summary="one row",
        content_sha256=sha256(content.encode()).hexdigest(),
        source_refs=("query:1",),
    )
    history = (
        AssistantTurnGroup(group_type="assistant", provider_turn_ref=first_ref),
        ToolExchangeGroup(
            group_type="tool_exchange",
            provider_turn_ref=second_ref,
            tool_calls=(call,),
            tool_results=(result,),
        ),
    )
    transport = RecordingTransport([fixture_response("final_success.json")])
    model, client = gateway(transport, store=store)
    try:
        await model.complete(request.model_copy(update={"history": history, "sequence": 2}))
    finally:
        await client.aclose()
    messages = json.loads(transport.requests[0].content)["messages"]
    assistants = [message for message in messages if message["role"] == "assistant"]
    assert [message["reasoning_content"] for message in assistants] == [
        "private 0",
        "private 1",
    ]
    assert [message["role"] for message in messages] == [
        "system",
        "assistant",
        "assistant",
        "tool",
        "user",
    ]


@pytest.mark.asyncio
async def test_rate_limit_exhaustion_uses_stable_error() -> None:
    transport = RecordingTransport([httpx.Response(429) for _ in range(3)])
    model, client = gateway(transport)
    try:
        with pytest.raises(ModelRateLimited) as caught:
            await model.complete(model_request())
    finally:
        await client.aclose()
    assert caught.value.reason_code == "provider_rate_limited"
    assert len(caught.value.attempts) == 3


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(401, ModelAuthError), (402, ModelBalanceError)],
)
@pytest.mark.asyncio
async def test_auth_and_balance_errors_are_terminal(status, error_type) -> None:  # type: ignore[no-untyped-def]
    transport = RecordingTransport([httpx.Response(status)])
    model, client = gateway(transport)
    try:
        with pytest.raises(error_type) as caught:
            await model.complete(model_request())
    finally:
        await client.aclose()
    assert caught.value.retryable is False
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    ("error", "charge_ambiguous"),
    [
        (httpx.ConnectError("connect failed"), False),
        (httpx.ReadTimeout("read timed out"), True),
    ],
)
@pytest.mark.asyncio
async def test_transport_exhaustion_preserves_charge_ambiguity(
    error: httpx.TransportError, charge_ambiguous: bool
) -> None:
    transport = RecordingTransport([error, error, error])
    model, client = gateway(transport)
    try:
        with pytest.raises(ModelTransportError) as caught:
            await model.complete(model_request())
    finally:
        await client.aclose()
    assert caught.value.charge_ambiguous is charge_ambiguous
    assert len(caught.value.attempts) == 3


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
@pytest.mark.asyncio
async def test_incomplete_final_finish_reasons_remain_visible(finish_reason: str) -> None:
    payload = json.loads((FIXTURES / "final_success.json").read_text(encoding="utf-8"))
    payload["choices"][0]["finish_reason"] = finish_reason
    transport = RecordingTransport([httpx.Response(200, json=payload)])
    model, client = gateway(transport)
    try:
        response = await model.complete(model_request())
    finally:
        await client.aclose()
    assert response.finish_reason == finish_reason


@pytest.mark.asyncio
async def test_insufficient_resource_retries_then_surfaces_infrastructure_error() -> None:
    responses = [fixture_response("insufficient_resource.json") for _ in range(3)]
    transport = RecordingTransport(responses)
    model, client = gateway(transport)
    try:
        with pytest.raises(ModelTransportError) as caught:
            await model.complete(model_request())
    finally:
        await client.aclose()
    assert caught.value.reason_code == "insufficient_system_resource"
    assert [attempt.outcome for attempt in caught.value.attempts] == [
        "insufficient_system_resource",
        "insufficient_system_resource",
        "insufficient_system_resource",
    ]


@pytest.mark.asyncio
async def test_payload_parse_failure_logs_sanitized_diagnostic(caplog) -> None:
    """Pit 72: fail-closed parse branches must emit sanitized metadata
    (structure, classes, positions — never response content) so repeated
    live failures are diagnosable offline instead of burning paid runs."""
    payload = json.loads((FIXTURES / "final_success.json").read_text(encoding="utf-8"))
    payload["choices"] = []
    transport = RecordingTransport([httpx.Response(200, json=payload)])
    model, client = gateway(transport)
    with caplog.at_level(logging.WARNING, logger="commerce_agent.model.gateway"):
        try:
            await model.complete(model_request())
        except ModelProtocolError:
            pass
        finally:
            await client.aclose()
    messages = [
        record.getMessage()
        for record in caplog.records
        if "protocol_diagnostic" in record.getMessage()
    ]
    assert len(messages) == 1
    msg = messages[0]
    assert "site=payload_parse" in msg
    assert "error=ValueError" in msg
    assert "finish_reason=None" in msg
    assert "PRIVATE_REASONING_SENTINEL" not in msg


@pytest.mark.asyncio
async def test_output_parse_failure_logs_sanitized_diagnostic(caplog) -> None:
    payload = json.loads((FIXTURES / "final_success.json").read_text(encoding="utf-8"))
    payload["choices"][0]["finish_reason"] = "tool_calls"
    payload["choices"][0]["message"]["tool_calls"] = [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "execute_readonly_sql",
                "arguments": '{"sql": "SELECT',  # truncated tool-call output
            },
        }
    ]
    transport = RecordingTransport([httpx.Response(200, json=payload)])
    model, client = gateway(transport)
    with caplog.at_level(logging.WARNING, logger="commerce_agent.model.gateway"):
        try:
            await model.complete(model_request())
        except ModelProtocolError:
            pass
        finally:
            await client.aclose()
    messages = [
        record.getMessage()
        for record in caplog.records
        if "protocol_diagnostic" in record.getMessage()
    ]
    assert len(messages) == 1
    msg = messages[0]
    assert "site=output_parse" in msg
    assert "error=JSONDecodeError" in msg
    assert "finish_reason='tool_calls'" in msg
    assert "model_echo_match=True" in msg
    assert "SELECT" not in msg
    assert "PRIVATE_REASONING_SENTINEL" not in msg
