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
    ToolResult,
)
from commerce_agent.model.errors import ModelTransportError
from commerce_agent.model.fake import FakeModel
from commerce_agent.orchestration.bird_a_graph import BirdAGraph
from commerce_agent.orchestration.contracts import BirdARunRequest, StopOutcome
from commerce_agent.orchestration.tools import SyntheticBirdAToolPort, ToolContractError

CONFIG_ROOT = Path(__file__).parents[3] / "configs" / "model"
RUN_ID = UUID("00000000-0000-0000-0000-000000000901")
ATTEMPT_ID = UUID("00000000-0000-0000-0000-000000000902")
NOW = datetime(2026, 9, 6, 2, 0, tzinfo=UTC)
SYNTHETIC_MANIFEST = json.loads(
    (
        Path(__file__).parents[2]
        / "fixtures"
        / "orchestration"
        / "bird-a-runtime.synthetic.v1.json"
    ).read_text(encoding="utf-8")
)


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


def context_builder(registry: ProfileRegistry) -> ContextBuilder:
    return ContextBuilder(
        registry=registry,
        estimator=FixedEstimator(),
        requested_model="deepseek-chat",
        timeout_seconds=Decimal(30),
        provider_user_id="b" * 32,
    )


def bird_a_request(
    registry: ProfileRegistry,
    *,
    max_model_calls: int = 6,
    max_tool_calls: int = 2,
) -> BirdARunRequest:
    profile = registry.get("bird_a")
    inference = next(rule.inference for rule in profile.inference_rules)
    scope = RunScope(
        run_id=RUN_ID,
        track="bird",
        mode="a",
        subject_id="synthetic-a",
        experiment_id="day3",
        config_hash=compute_config_hash(profile, inference, "deepseek-chat"),
    )
    content = "Count synthetic order statuses"
    return BirdARunRequest(
        run_scope=scope,
        attempt_id=ATTEMPT_ID,
        current_input=ContextDatum(
            kind="user_input",
            namespace="user_input",
            source_ref="synthetic:request",
            revision=None,
            content=content,
            digest=sha256(content.encode("utf-8")).hexdigest(),
        ),
        max_model_calls=max_model_calls,
        max_tool_calls=max_tool_calls,
    )


def model_response(
    scope: RunScope,
    *,
    sequence: int,
    output: FinalOutput | ToolCallOutput,
    finish_reason: str = "stop",
) -> ModelResponse:
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
    expected_ids = (
        tuple(call.call_id for call in output.tool_calls)
        if isinstance(output, ToolCallOutput)
        else ()
    )
    return ModelResponse(
        output=output,
        provider_turn_ref=ProviderTurnRef(
            turn_id=UUID(int=sequence + 1),
            scope_digest=scope_digest(scope),
            attempt_id=ATTEMPT_ID,
            sequence=sequence,
            payload_sha256=f"{sequence + 1:064x}",
            expected_tool_call_ids=expected_ids,
            token_weight=2,
            expires_at=NOW,
        ),
        finish_reason=finish_reason,
        actual_model="fake",
        system_fingerprint="fake-v1",
        usage=usage,
        cost=cost,
        attempts=(attempt,),
    )


def bird_a_graph(
    registry: ProfileRegistry,
    gateway: FakeModel,
    *,
    turn_store: RecordingTurnStore | None = None,
) -> BirdAGraph:
    return BirdAGraph(
        context_builder=context_builder(registry),
        profile=registry.get("bird_a"),
        gateway=gateway,
        tool_port=SyntheticBirdAToolPort.from_fixture(SYNTHETIC_MANIFEST),
        turn_store=turn_store or RecordingTurnStore(),
    )


@pytest.mark.asyncio
async def test_bird_a_runs_active_tool_loop_to_completion() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = bird_a_request(registry)
    call = ToolCall(
        call_id="bird_call_1",
        name="synthetic_bird_a_observe_schema",
        arguments_json='{"schema_ref":"synthetic:orders"}',
    )
    gateway = FakeModel(
        [
            model_response(
                request.run_scope,
                sequence=0,
                output=ToolCallOutput(type="tool_calls", tool_calls=(call,)),
                finish_reason="tool_calls",
            ),
            model_response(
                request.run_scope,
                sequence=1,
                output=FinalOutput(
                    type="final",
                    content=("SELECT status, COUNT(*) FROM synthetic_orders GROUP BY status"),
                ),
            ),
        ]
    )

    outcome = await bird_a_graph(registry, gateway).run(request)

    assert outcome.status == "completed"
    assert outcome.model_calls == 2
    assert outcome.tool_calls == 1
    assert len(gateway.requests) == 2
    assert gateway.requests[1].history[-1].group_type == "tool_exchange"


class _RecordingSyntheticPort:
    """Structurally independent BirdToolPort fake that records every call."""

    def __init__(self, delegate: SyntheticBirdAToolPort) -> None:
        self._delegate = delegate
        self.calls: list[ToolCall] = []

    async def execute(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        return await self._delegate.execute(call)


@pytest.mark.asyncio
async def test_bird_a_graph_accepts_protocol_tool_port() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = bird_a_request(registry)
    call = ToolCall(
        call_id="bird_call_1",
        name="synthetic_bird_a_observe_schema",
        arguments_json='{"schema_ref":"synthetic:orders"}',
    )
    gateway = FakeModel(
        [
            model_response(
                request.run_scope,
                sequence=0,
                output=ToolCallOutput(type="tool_calls", tool_calls=(call,)),
                finish_reason="tool_calls",
            ),
            model_response(
                request.run_scope,
                sequence=1,
                output=FinalOutput(
                    type="final",
                    content=("SELECT status, COUNT(*) FROM synthetic_orders GROUP BY status"),
                ),
            ),
        ]
    )
    port = _RecordingSyntheticPort(SyntheticBirdAToolPort.from_fixture(SYNTHETIC_MANIFEST))
    graph = BirdAGraph(
        context_builder=context_builder(registry),
        profile=registry.get("bird_a"),
        gateway=gateway,
        tool_port=port,
        turn_store=RecordingTurnStore(),
    )

    outcome = await graph.run(request)

    assert outcome.status == "completed"
    assert outcome.model_calls == 2
    assert outcome.tool_calls == 1
    assert [recorded.name for recorded in port.calls] == ["synthetic_bird_a_observe_schema"]


@pytest.mark.asyncio
async def test_bird_a_stops_at_model_call_budget() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = bird_a_request(registry, max_model_calls=1)
    call = ToolCall(
        call_id="bird_call_1",
        name="synthetic_bird_a_observe_schema",
        arguments_json='{"schema_ref":"synthetic:orders"}',
    )
    gateway = FakeModel(
        [
            model_response(
                request.run_scope,
                sequence=0,
                output=ToolCallOutput(type="tool_calls", tool_calls=(call,)),
                finish_reason="tool_calls",
            )
        ]
    )

    outcome = await bird_a_graph(registry, gateway).run(request)

    assert outcome.status == "stopped"
    assert outcome.stop is not None
    assert outcome.stop.kind == "budget_exhausted"
    assert outcome.stop.reason_code == "model_call_limit"
    assert len(gateway.requests) == 1


@pytest.mark.asyncio
async def test_bird_a_stops_before_exceeding_tool_call_budget() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = bird_a_request(registry, max_tool_calls=0)
    call = ToolCall(
        call_id="bird_call_1",
        name="synthetic_bird_a_observe_schema",
        arguments_json='{"schema_ref":"synthetic:orders"}',
    )
    gateway = FakeModel(
        [
            model_response(
                request.run_scope,
                sequence=0,
                output=ToolCallOutput(type="tool_calls", tool_calls=(call,)),
                finish_reason="tool_calls",
            )
        ]
    )

    outcome = await bird_a_graph(registry, gateway).run(request)

    assert outcome.status == "stopped"
    assert outcome.stop is not None
    assert outcome.stop.kind == "budget_exhausted"
    assert outcome.stop.reason_code == "tool_call_limit"
    assert outcome.tool_calls == 0
    assert len(gateway.requests) == 1


@pytest.mark.asyncio
async def test_bird_a_stops_on_repeated_evidence_digest() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = bird_a_request(registry)
    calls = tuple(
        ToolCall(
            call_id=f"bird_call_{index}",
            name="synthetic_bird_a_observe_schema",
            arguments_json='{"schema_ref":"synthetic:orders"}',
        )
        for index in (1, 2)
    )
    gateway = FakeModel(
        [
            model_response(
                request.run_scope,
                sequence=index,
                output=ToolCallOutput(type="tool_calls", tool_calls=(call,)),
                finish_reason="tool_calls",
            )
            for index, call in enumerate(calls)
        ]
    )

    outcome = await bird_a_graph(registry, gateway).run(request)

    assert outcome.status == "stopped"
    assert outcome.stop is not None
    assert outcome.stop.kind == "no_progress"
    assert outcome.stop.reason_code == "repeated_evidence"
    assert outcome.stop.evidence_digest is not None
    assert outcome.model_calls == 2
    assert outcome.tool_calls == 2
    assert len(gateway.requests) == 2


@pytest.mark.asyncio
async def test_bird_a_maps_filtered_output_to_unsafe_stop() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = bird_a_request(registry)
    gateway = FakeModel(
        [
            model_response(
                request.run_scope,
                sequence=0,
                output=FinalOutput(type="final", content="filtered"),
                finish_reason="content_filter",
            )
        ]
    )

    outcome = await bird_a_graph(registry, gateway).run(request)

    assert outcome.status == "stopped"
    assert outcome.stop is not None
    assert outcome.stop.kind == "unsafe"
    assert outcome.stop.reason_code == "model_output_filtered"
    assert outcome.model_calls == 1


@pytest.mark.asyncio
async def test_bird_a_maps_length_output_to_insufficient_data_stop() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = bird_a_request(registry)
    gateway = FakeModel(
        [
            model_response(
                request.run_scope,
                sequence=0,
                output=FinalOutput(type="final", content="incomplete"),
                finish_reason="length",
            )
        ]
    )

    outcome = await bird_a_graph(registry, gateway).run(request)

    assert outcome.status == "stopped"
    assert outcome.stop is not None
    assert outcome.stop.kind == "insufficient_data"
    assert outcome.stop.reason_code == "model_output_incomplete"
    assert outcome.model_calls == 1


@pytest.mark.asyncio
async def test_bird_a_maps_provider_failure_to_infrastructure_stop() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = bird_a_request(registry)
    gateway = FakeModel(
        [
            ModelTransportError(
                "fixture_transport_exhausted",
                "PRIVATE_PROVIDER_SENTINEL",
                retryable=True,
            )
        ]
    )

    outcome = await bird_a_graph(registry, gateway).run(request)

    assert outcome.status == "stopped"
    assert outcome.stop is not None
    assert outcome.stop.kind == "infrastructure_error"
    assert outcome.stop.reason_code == "fixture_transport_exhausted"
    assert outcome.stop.retryable is True
    assert outcome.model_calls == 1
    assert "PRIVATE_PROVIDER_SENTINEL" not in str(outcome)


@pytest.mark.asyncio
async def test_bird_a_maps_resource_finish_to_infrastructure_stop() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = bird_a_request(registry)
    gateway = FakeModel(
        [
            model_response(
                request.run_scope,
                sequence=0,
                output=FinalOutput(type="final", content="resource unavailable"),
                finish_reason="insufficient_system_resource",
            )
        ]
    )

    outcome = await bird_a_graph(registry, gateway).run(request)

    assert outcome.status == "stopped"
    assert outcome.stop is not None
    assert outcome.stop.kind == "infrastructure_error"
    assert outcome.stop.reason_code == "insufficient_system_resource"
    assert outcome.stop.retryable is True


@pytest.mark.parametrize("terminal", ["completed", "budget", "provider_error", "tool_error"])
@pytest.mark.asyncio
async def test_bird_a_clears_private_state_on_every_terminal_path(terminal: str) -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = bird_a_request(registry)
    if terminal == "completed":
        script = [
            model_response(
                request.run_scope,
                sequence=0,
                output=FinalOutput(type="final", content="SELECT 1"),
            )
        ]
    elif terminal == "budget":
        request = bird_a_request(registry, max_model_calls=1)
        script = [
            model_response(
                request.run_scope,
                sequence=0,
                output=ToolCallOutput(
                    type="tool_calls",
                    tool_calls=(
                        ToolCall(
                            call_id="bird_call_1",
                            name="synthetic_bird_a_observe_schema",
                            arguments_json='{"schema_ref":"synthetic:orders"}',
                        ),
                    ),
                ),
                finish_reason="tool_calls",
            )
        ]
    elif terminal == "provider_error":
        script = [
            ModelTransportError(
                "fixture_transport_exhausted",
                "fixture transport failed",
                retryable=True,
            )
        ]
    else:
        script = [
            model_response(
                request.run_scope,
                sequence=0,
                output=ToolCallOutput(
                    type="tool_calls",
                    tool_calls=(
                        ToolCall(
                            call_id="bird_call_1",
                            name="execute_readonly_sql",
                            arguments_json='{"sql":"SELECT 1"}',
                        ),
                    ),
                ),
                finish_reason="tool_calls",
            )
        ]
    store = RecordingTurnStore()
    graph = bird_a_graph(registry, FakeModel(script), turn_store=store)

    if terminal == "tool_error":
        with pytest.raises(ToolContractError, match="tool contract validation failed"):
            await graph.run(request)
    else:
        await graph.run(request)

    assert store.deleted_attempts == [
        AttemptRef(
            scope_digest=scope_digest(request.run_scope),
            attempt_id=ATTEMPT_ID,
        )
    ]


class _ScriptedGate:
    """Structural `BirdAModelTurnGate` fake scripting per-turn stop decisions."""

    def __init__(self, stops: list[StopOutcome | None]) -> None:
        self._stops = list(stops)
        self.calls = 0

    async def stop_model_turn(self) -> StopOutcome | None:
        self.calls += 1
        if self._stops:
            return self._stops.pop(0)
        return None


@pytest.mark.asyncio
async def test_bird_a_gate_stops_attempt_before_first_model_call() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = bird_a_request(registry)
    gateway = FakeModel([])  # any model call would exhaust the script and fail
    turn_store = RecordingTurnStore()
    gate = _ScriptedGate(
        [
            StopOutcome(
                kind="budget_exhausted",
                reason_code="coin_budget_signal",
                retryable=False,
            )
        ]
    )
    graph = BirdAGraph(
        context_builder=context_builder(registry),
        profile=registry.get("bird_a"),
        gateway=gateway,
        tool_port=SyntheticBirdAToolPort.from_fixture(SYNTHETIC_MANIFEST),
        turn_store=turn_store,
        model_turn_gate=gate,
    )

    outcome = await graph.run(request)

    assert outcome.status == "stopped"
    assert outcome.stop is not None
    assert outcome.stop.kind == "budget_exhausted"
    assert outcome.stop.reason_code == "coin_budget_signal"
    assert outcome.model_calls == 0
    assert outcome.tool_calls == 0
    assert len(gateway.requests) == 0
    assert [ref.attempt_id for ref in turn_store.deleted_attempts] == [ATTEMPT_ID]


@pytest.mark.asyncio
async def test_bird_a_gate_consulted_before_every_model_call() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = bird_a_request(registry)
    call = ToolCall(
        call_id="bird_call_1",
        name="synthetic_bird_a_observe_schema",
        arguments_json='{"schema_ref":"synthetic:orders"}',
    )
    gateway = FakeModel(
        [
            model_response(
                request.run_scope,
                sequence=0,
                output=ToolCallOutput(type="tool_calls", tool_calls=(call,)),
                finish_reason="tool_calls",
            ),
            model_response(
                request.run_scope,
                sequence=1,
                output=FinalOutput(
                    type="final",
                    content="SELECT status FROM synthetic_orders",
                ),
            ),
        ]
    )
    gate = _ScriptedGate([])
    graph = BirdAGraph(
        context_builder=context_builder(registry),
        profile=registry.get("bird_a"),
        gateway=gateway,
        tool_port=SyntheticBirdAToolPort.from_fixture(SYNTHETIC_MANIFEST),
        turn_store=RecordingTurnStore(),
        model_turn_gate=gate,
    )

    outcome = await graph.run(request)

    assert outcome.status == "completed"
    assert gate.calls == 2
