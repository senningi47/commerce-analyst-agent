import json
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from commerce_agent.context_builder._tokens import TokenEstimate
from commerce_agent.context_builder.builder import ContextBuilder, compute_config_hash, scope_digest
from commerce_agent.context_builder.contracts import ContextDatum
from commerce_agent.context_builder.profiles import ProfileRegistry
from commerce_agent.model._turn_store import AttemptRef
from commerce_agent.model.contracts import (
    CostUnavailable,
    FinalOutput,
    ModelAttemptSummary,
    ModelResponse,
    ProviderTurnRef,
    ReportedUsage,
    RunScope,
    ToolCall,
    ToolCallOutput,
)
from commerce_agent.model.errors import ModelTransportError
from commerce_agent.model.fake import FakeModel
from commerce_agent.orchestration.bird_c_responder import BirdCResponder
from commerce_agent.orchestration.contracts import (
    AskUserCandidate,
    BirdCRequest,
    SubmitSqlCandidate,
    TextCandidate,
)
from commerce_agent.orchestration.tools import ToolContractError

CONFIG_ROOT = Path(__file__).parents[3] / "configs" / "model"
RUN_ID = UUID("00000000-0000-0000-0000-000000000911")
ATTEMPT_ID = UUID("00000000-0000-0000-0000-000000000912")
NOW = datetime(2026, 9, 6, 3, 0, tzinfo=UTC)


class FixedEstimator:
    @property
    def revision(self) -> str:
        return "deepseek-tokenizer-v1"

    def estimate(self, request: object) -> TokenEstimate:
        del request
        return TokenEstimate(input_tokens=100, estimator_revision=self.revision)


class RecordingTurnStore:
    def __init__(self) -> None:
        self.deleted_attempts: list[AttemptRef] = []

    async def delete_attempt(self, attempt: AttemptRef) -> None:
        self.deleted_attempts.append(attempt)


def bird_c_request(registry: ProfileRegistry) -> BirdCRequest:
    profile = registry.get("bird_c")
    inference = next(rule.inference for rule in profile.inference_rules)
    scope = RunScope(
        run_id=RUN_ID,
        track="bird",
        mode="c",
        subject_id="synthetic-c",
        experiment_id="day3",
        config_hash=compute_config_hash(profile, inference, "deepseek-chat"),
    )
    phase = "synthetic clarification phase"
    return BirdCRequest(
        run_scope=scope,
        attempt_id=ATTEMPT_ID,
        current_phase=ContextDatum(
            kind="phase",
            namespace="bird_c_phase",
            source_ref="synthetic:phase",
            revision="synthetic-phase-v1",
            content=phase,
            digest=sha256(phase.encode("utf-8")).hexdigest(),
        ),
    )


def model_response(scope: RunScope, calls: tuple[ToolCall, ...]) -> ModelResponse:
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
        sent_at=NOW,
        completed_at=NOW,
        outcome="success",
        http_status=200,
        retryable=False,
        charge_ambiguous=False,
        usage=usage,
        cost=cost,
    )
    return ModelResponse(
        output=ToolCallOutput(type="tool_calls", tool_calls=calls),
        provider_turn_ref=ProviderTurnRef(
            turn_id=UUID(int=1),
            scope_digest=scope_digest(scope),
            attempt_id=ATTEMPT_ID,
            sequence=0,
            payload_sha256="1" * 64,
            expected_tool_call_ids=tuple(call.call_id for call in calls),
            token_weight=2,
            expires_at=NOW,
        ),
        finish_reason="tool_calls",
        actual_model="fake",
        system_fingerprint="fake-v1",
        usage=usage,
        cost=cost,
        attempts=(attempt,),
    )


def bird_c_responder(
    registry: ProfileRegistry,
    gateway: FakeModel,
    *,
    turn_store: RecordingTurnStore | None = None,
) -> BirdCResponder:
    return BirdCResponder(
        context_builder=ContextBuilder(
            registry=registry,
            estimator=FixedEstimator(),
            requested_model="deepseek-chat",
            timeout_seconds=Decimal(30),
            provider_user_id="c" * 32,
        ),
        profile=registry.get("bird_c"),
        gateway=gateway,
        turn_store=turn_store or RecordingTurnStore(),
    )


@pytest.mark.parametrize(
    ("tool_name", "arguments", "candidate_type"),
    [
        ("ask_user", {"question": "Which date field?"}, AskUserCandidate),
        (
            "submit_sql",
            {"sql": "SELECT COUNT(*) FROM synthetic_orders"},
            SubmitSqlCandidate,
        ),
    ],
)
@pytest.mark.asyncio
async def test_bird_c_returns_one_typed_candidate(
    tool_name: str,
    arguments: dict[str, str],
    candidate_type: type,
) -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = bird_c_request(registry)
    call = ToolCall(
        call_id="c_call",
        name=tool_name,
        arguments_json=json.dumps(arguments, sort_keys=True, separators=(",", ":")),
    )
    gateway = FakeModel([model_response(request.run_scope, (call,))])

    response = await bird_c_responder(registry, gateway).respond(request)

    assert isinstance(response.candidate, candidate_type)
    assert len(gateway.requests) == 1
    assert gateway.requests[0].history == ()


@pytest.mark.parametrize(
    ("case", "reason_code"),
    [
        ("zero_calls", "bird_c_single_candidate_required"),
        ("multiple_calls", "bird_c_single_candidate_required"),
        ("bird_a_tool", "tool_not_registered"),
        ("unknown_field", "invalid_tool_arguments"),
        ("malformed_json", "invalid_tool_arguments"),
    ],
)
@pytest.mark.asyncio
async def test_bird_c_rejects_invalid_candidate_shapes_without_looping(
    case: str,
    reason_code: str,
) -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = bird_c_request(registry)
    valid_call = ToolCall(
        call_id="c_call",
        name="ask_user",
        arguments_json='{"question":"Which date field?"}',
    )
    response = model_response(request.run_scope, (valid_call,))
    if False:  # final_text/length/content_filter moved to the positive test below
        response = response.model_copy(
            update={
                "output": FinalOutput(type="final", content="not a candidate"),
                "finish_reason": {
                    "final_text": "stop",
                    "length": "length",
                    "content_filter": "content_filter",
                }[case],
                "provider_turn_ref": response.provider_turn_ref.model_copy(
                    update={"expected_tool_call_ids": ()}
                ),
            }
        )
    elif case == "zero_calls":
        response = response.model_copy(
            update={
                "output": ToolCallOutput.model_construct(type="tool_calls", tool_calls=()),
                "provider_turn_ref": response.provider_turn_ref.model_copy(
                    update={"expected_tool_call_ids": ()}
                ),
            }
        )
    elif case == "multiple_calls":
        second = valid_call.model_copy(update={"call_id": "c_call_2"})
        response = model_response(request.run_scope, (valid_call, second))
    elif case == "bird_a_tool":
        call = valid_call.model_copy(
            update={
                "name": "synthetic_bird_a_observe_schema",
                "arguments_json": '{"schema_ref":"synthetic:orders"}',
            }
        )
        response = model_response(request.run_scope, (call,))
    elif case == "unknown_field":
        call = valid_call.model_copy(
            update={"arguments_json": '{"question":"safe","unexpected":"blocked"}'}
        )
        response = model_response(request.run_scope, (call,))
    else:
        call = ToolCall.model_construct(
            call_id="c_call",
            name="ask_user",
            arguments_json='{"question":"broken"',
        )
        response = model_response(request.run_scope, (call,))
    gateway = FakeModel([response])

    with pytest.raises(ToolContractError) as caught:
        await bird_c_responder(registry, gateway).respond(request)

    assert caught.value.reason_code == reason_code
    assert len(gateway.requests) == 1


@pytest.mark.parametrize("terminal", ["response", "validation_error", "provider_error"])
@pytest.mark.asyncio
async def test_bird_c_clears_private_state_after_response_or_error(terminal: str) -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = bird_c_request(registry)
    arguments = (
        '{"question":"Which date field?"}'
        if terminal != "validation_error"
        else '{"question":"safe","unexpected":"blocked"}'
    )
    call = ToolCall(
        call_id="c_call",
        name="ask_user",
        arguments_json=arguments,
    )
    store = RecordingTurnStore()
    script = (
        [
            ModelTransportError(
                "fixture_transport_exhausted",
                "fixture transport failed",
                retryable=True,
            )
        ]
        if terminal == "provider_error"
        else [model_response(request.run_scope, (call,))]
    )
    responder = bird_c_responder(registry, FakeModel(script), turn_store=store)

    if terminal == "validation_error":
        with pytest.raises(ToolContractError):
            await responder.respond(request)
    elif terminal == "provider_error":
        with pytest.raises(ModelTransportError):
            await responder.respond(request)
    else:
        response = await responder.respond(request)
        assert "provider_turn_ref" not in type(response).model_fields

    assert store.deleted_attempts == [
        AttemptRef(
            scope_digest=scope_digest(request.run_scope),
            attempt_id=ATTEMPT_ID,
        )
    ]


@pytest.mark.parametrize("finish_reason", ["stop", "length", "content_filter"])
@pytest.mark.asyncio
async def test_bird_c_returns_text_candidate_for_non_tool_turns(finish_reason: str) -> None:
    """Official ADK semantics (run f verdict 2026-09-15): a non-function-call
    response ENDS the runner invocation — it is not a contract violation. The
    responder surfaces the prose as a text candidate instead of failing the
    episode; the orchestrator's next phase message continues from memory."""
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = bird_c_request(registry)
    valid_call = ToolCall(
        call_id="c_call",
        name="ask_user",
        arguments_json='{"question":"Which date field?"}',
    )
    response = model_response(request.run_scope, (valid_call,)).model_copy(
        update={
            "output": FinalOutput(type="final", content="I will fix the join and resubmit."),
            "finish_reason": finish_reason,
            "provider_turn_ref": model_response(request.run_scope, (valid_call,))
            .provider_turn_ref.model_copy(update={"expected_tool_call_ids": ()}),
        }
    )
    gateway = FakeModel([response])

    result = await bird_c_responder(registry, gateway).respond(request)

    assert result.candidate == TextCandidate(
        type="text", content="I will fix the join and resubmit."
    )
    assert len(gateway.requests) == 1
