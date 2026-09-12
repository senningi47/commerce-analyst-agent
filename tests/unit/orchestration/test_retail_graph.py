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
from commerce_agent.knowledge._store import StoredKnowledgeCatalog
from commerce_agent.knowledge.contracts import KnowledgeEvidence, KnowledgeKind
from commerce_agent.knowledge.module import KnowledgeModule
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
    ToolExchangeGroup,
)
from commerce_agent.model.errors import ModelTransportError
from commerce_agent.model.fake import FakeModel
from commerce_agent.orchestration._checkpoint import (
    CheckpointIncompatible,
    CheckpointInfrastructureError,
    create_memory_saver,
)
from commerce_agent.orchestration.contracts import RetailRunRequest
from commerce_agent.orchestration.retail_graph import RetailGraph, RetailLoopPolicy
from commerce_agent.orchestration.tools import (
    RetailToolDispatcher,
    ToolContractError,
    ToolInfrastructureError,
)
from commerce_agent.query_engine._ast_policy import AstPolicy, ValidatedQuery
from commerce_agent.query_engine.contracts import QueryResult
from commerce_agent.query_engine.engine import QueryEngine

CONFIG_ROOT = Path(__file__).parents[3] / "configs" / "model"
RUN_ID = UUID("00000000-0000-0000-0000-000000000811")
ATTEMPT_ID = UUID("00000000-0000-0000-0000-000000000812")
NOW = datetime(2026, 9, 6, 1, 0, tzinfo=UTC)


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


class FailOnceCleanupStore(RecordingTurnStore):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def delete_attempt(self, attempt: AttemptRef) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("PRIVATE_CLEANUP_SENTINEL")
        await super().delete_attempt(attempt)


class InMemoryKnowledgeStore:
    async def load_retail_catalog(self) -> StoredKnowledgeCatalog:
        revision = "retail-catalog-v1"
        return StoredKnowledgeCatalog(
            revision=revision,
            content_sha256="b" * 64,
            evidence=(
                KnowledgeEvidence(
                    doc_id="metric.gmv",
                    revision=revision,
                    kind=KnowledgeKind.METRIC,
                    title="GMV",
                    content={"status": "clarification_required"},
                    source_type="test_fixture",
                    source_path="tests/unit/orchestration/test_retail_graph.py",
                    source_sha256="c" * 64,
                ),
            ),
        )


class AggregateExecutor:
    async def execute(self, query: ValidatedQuery) -> QueryResult:
        del query
        return QueryResult(columns=["order_count"], rows=[{"order_count": 99_441}])


class FailOnceReadonlyDispatcher:
    def __init__(self, wrapped: RetailToolDispatcher) -> None:
        self.wrapped = wrapped
        self.failed = False
        self.execution_fingerprints: list[str] = []

    async def execute(
        self,
        scope: RunScope,
        attempt_id: UUID,
        call: ToolCall,
    ):
        fingerprint = sha256(
            (
                scope.model_dump_json()
                + str(attempt_id)
                + call.model_dump_json()
            ).encode("utf-8")
        ).hexdigest()
        self.execution_fingerprints.append(fingerprint)
        if not self.failed:
            self.failed = True
            raise ToolInfrastructureError("fixture_transient_failure")
        return await self.wrapped.execute(scope, attempt_id, call)


def graph_scope(registry: ProfileRegistry) -> RunScope:
    profile = registry.get("retail")
    inference = next(rule.inference for rule in profile.inference_rules if rule.step == "retail_decide")
    return RunScope(
        run_id=RUN_ID,
        track="retail",
        mode="retail",
        subject_id="retail-demo",
        experiment_id="day3",
        config_hash=compute_config_hash(profile, inference, "deepseek-chat"),
    )


def retail_request(registry: ProfileRegistry, content: str) -> RetailRunRequest:
    scope = graph_scope(registry)
    return RetailRunRequest(
        run_scope=scope,
        attempt_id=ATTEMPT_ID,
        current_input=ContextDatum(
            kind="user_input",
            namespace="user_input",
            source_ref="request:current",
            revision=None,
            content=content,
            digest=sha256(content.encode("utf-8")).hexdigest(),
        ),
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
    expected_call_ids = (
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
            expected_tool_call_ids=expected_call_ids,
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


def context_builder(registry: ProfileRegistry) -> ContextBuilder:
    return ContextBuilder(
        registry=registry,
        estimator=FixedEstimator(),
        requested_model="deepseek-chat",
        timeout_seconds=Decimal(30),
        provider_user_id="a" * 32,
    )


@pytest.mark.asyncio
async def test_retail_graph_completes_one_fake_model_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = retail_request(registry, "How many order statuses exist?")
    gateway = FakeModel(
        [
            model_response(
                request.run_scope,
                sequence=0,
                output=FinalOutput(type="final", content="There are 8 statuses."),
            )
        ]
    )
    graph = RetailGraph(
        context_builder=context_builder(registry),
        profile=registry.get("retail"),
        gateway=gateway,
        dispatcher=None,
        checkpointer=create_memory_saver(),
        turn_store=RecordingTurnStore(),
    )

    outcome = await graph.run(request)

    assert outcome.status == "stopped"
    assert outcome.stop.reason_code == "typed_retail_terminal_required"
    assert outcome.model_calls == 1
    assert outcome.tool_calls == 0
    assert len(gateway.requests) == 1


@pytest.mark.asyncio
async def test_retail_graph_closes_tool_exchange_before_second_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = retail_request(registry, "What is GMV?")
    call = ToolCall(
        call_id="call_1",
        name="retrieve_retail_knowledge",
        arguments_json='{\"question\":\"GMV\"}',
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
                output=FinalOutput(type="final", content="GMV requires clarification."),
            ),
        ]
    )
    graph = RetailGraph(
        context_builder=context_builder(registry),
        profile=registry.get("retail"),
        gateway=gateway,
        dispatcher=RetailToolDispatcher(
            knowledge=KnowledgeModule(InMemoryKnowledgeStore()),
            resolver=None,
            query_engine=None,
        ),
        checkpointer=create_memory_saver(),
        turn_store=RecordingTurnStore(),
    )

    outcome = await graph.run(request)

    assert outcome.status == "stopped"
    assert outcome.model_calls == 2
    assert outcome.tool_calls == 1
    second = gateway.requests[1]
    assert isinstance(second.history[-1], ToolExchangeGroup)
    assert second.history[-1].tool_results[0].call_id == "call_1"


@pytest.mark.asyncio
async def test_resume_retries_readonly_tool_without_calling_model_or_resetting_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = retail_request(registry, "Count statuses")
    call = ToolCall(
        call_id="call_1",
        name="execute_readonly_sql",
        arguments_json=(
            '{"sql":"SELECT COUNT(*) AS order_count FROM retail.orders"}'
        ),
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
                output=FinalOutput(type="final", content="99441"),
            ),
        ]
    )
    dispatcher = FailOnceReadonlyDispatcher(
        RetailToolDispatcher(
            knowledge=None,
            resolver=None,
            query_engine=QueryEngine(policy=AstPolicy(), executor=AggregateExecutor()),
        )
    )
    turn_store = RecordingTurnStore()
    graph = RetailGraph(
        context_builder=context_builder(registry),
        profile=registry.get("retail"),
        gateway=gateway,
        dispatcher=dispatcher,  # type: ignore[arg-type]
        checkpointer=create_memory_saver(),
        turn_store=turn_store,
    )

    with pytest.raises(ToolInfrastructureError):
        await graph.run(request)
    assert turn_store.deleted_attempts == []
    resumed = await graph.run(request)

    assert resumed.status == "stopped"
    assert len(gateway.requests) == 2
    assert gateway.requests[0].sequence == 0
    assert gateway.requests[1].sequence == 1
    assert resumed.model_calls == 2
    assert len(dispatcher.execution_fingerprints) == 2
    assert dispatcher.execution_fingerprints[0] == dispatcher.execution_fingerprints[1]


@pytest.mark.asyncio
async def test_retail_graph_stops_on_repeated_evidence_without_another_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = retail_request(registry, "What is GMV?")
    calls = tuple(
        ToolCall(
            call_id=f"call_{index}",
            name="retrieve_retail_knowledge",
            arguments_json='{\"question\":\"GMV\"}',
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
    graph = RetailGraph(
        context_builder=context_builder(registry),
        profile=registry.get("retail"),
        gateway=gateway,
        dispatcher=RetailToolDispatcher(
            knowledge=KnowledgeModule(InMemoryKnowledgeStore()),
            resolver=None,
            query_engine=None,
        ),
        checkpointer=create_memory_saver(),
        turn_store=RecordingTurnStore(),
    )

    outcome = await graph.run(request)

    assert outcome.status == "stopped"
    assert outcome.stop is not None
    assert outcome.stop.kind == "no_progress"
    assert outcome.stop.reason_code == "repeated_evidence"
    assert outcome.model_calls == 2
    assert outcome.tool_calls == 2
    assert len(gateway.requests) == 2


@pytest.mark.asyncio
async def test_retail_graph_stops_before_seventh_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = retail_request(registry, "Keep checking distinct definitions")
    gateway = FakeModel(
        [
            model_response(
                request.run_scope,
                sequence=index,
                output=ToolCallOutput(
                    type="tool_calls",
                    tool_calls=(
                        ToolCall(
                            call_id=f"call_{index}",
                            name="retrieve_retail_knowledge",
                            arguments_json=f'{{"question":"definition {index}"}}',
                        ),
                    ),
                ),
                finish_reason="tool_calls",
            )
            for index in range(6)
        ]
    )
    graph = RetailGraph(
        context_builder=context_builder(registry),
        profile=registry.get("retail"),
        gateway=gateway,
        dispatcher=RetailToolDispatcher(
            knowledge=KnowledgeModule(InMemoryKnowledgeStore()),
            resolver=None,
            query_engine=None,
        ),
        checkpointer=create_memory_saver(),
        turn_store=RecordingTurnStore(),
    )

    outcome = await graph.run(request)

    assert outcome.status == "stopped"
    assert outcome.stop is not None
    assert outcome.stop.kind == "budget_exhausted"
    assert outcome.stop.reason_code == "model_call_limit"
    assert outcome.model_calls == 6
    assert len(gateway.requests) == 6


@pytest.mark.asyncio
async def test_retail_graph_stops_before_thirteenth_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = retail_request(registry, "Inspect definitions within budget")

    def calls(start: int, count: int) -> tuple[ToolCall, ...]:
        return tuple(
            ToolCall(
                call_id=f"call_{index}",
                name="retrieve_retail_knowledge",
                arguments_json=f'{{"question":"definition {index}"}}',
            )
            for index in range(start, start + count)
        )

    gateway = FakeModel(
        [
            model_response(
                request.run_scope,
                sequence=0,
                output=ToolCallOutput(type="tool_calls", tool_calls=calls(0, 6)),
                finish_reason="tool_calls",
            ),
            model_response(
                request.run_scope,
                sequence=1,
                output=ToolCallOutput(type="tool_calls", tool_calls=calls(6, 6)),
                finish_reason="tool_calls",
            ),
            model_response(
                request.run_scope,
                sequence=2,
                output=ToolCallOutput(type="tool_calls", tool_calls=calls(12, 1)),
                finish_reason="tool_calls",
            ),
        ]
    )
    graph = RetailGraph(
        context_builder=context_builder(registry),
        profile=registry.get("retail"),
        gateway=gateway,
        dispatcher=RetailToolDispatcher(
            knowledge=KnowledgeModule(InMemoryKnowledgeStore()),
            resolver=None,
            query_engine=None,
        ),
        checkpointer=create_memory_saver(),
        turn_store=RecordingTurnStore(),
    )

    outcome = await graph.run(request)

    assert outcome.status == "stopped"
    assert outcome.stop is not None
    assert outcome.stop.kind == "budget_exhausted"
    assert outcome.stop.reason_code == "tool_call_limit"
    assert outcome.model_calls == 3
    assert outcome.tool_calls == 12


@pytest.mark.parametrize("terminal", ["completed", "budget", "unsafe", "infrastructure"])
@pytest.mark.asyncio
async def test_retail_finalize_clears_private_turns_exactly_once(
    terminal: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = retail_request(registry, "safe aggregate")
    dispatcher = None
    loop_policy = RetailLoopPolicy()
    if terminal == "budget":
        call = ToolCall(
            call_id="call_1",
            name="retrieve_retail_knowledge",
            arguments_json='{\"question\":\"GMV\"}',
        )
        script = [
            model_response(
                request.run_scope,
                sequence=0,
                output=ToolCallOutput(type="tool_calls", tool_calls=(call,)),
                finish_reason="tool_calls",
            )
        ]
        dispatcher = RetailToolDispatcher(
            knowledge=KnowledgeModule(InMemoryKnowledgeStore()),
            resolver=None,
            query_engine=None,
        )
        loop_policy = RetailLoopPolicy(max_model_calls=1)
    elif terminal == "unsafe":
        script = [
            model_response(
                request.run_scope,
                sequence=0,
                output=FinalOutput(type="final", content="filtered"),
                finish_reason="content_filter",
            )
        ]
    elif terminal == "infrastructure":
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
                output=FinalOutput(type="final", content="done"),
            )
        ]
    turn_store = RecordingTurnStore()
    graph = RetailGraph(
        context_builder=context_builder(registry),
        profile=registry.get("retail"),
        gateway=FakeModel(script),
        dispatcher=dispatcher,
        checkpointer=create_memory_saver(),
        turn_store=turn_store,
        loop_policy=loop_policy,
    )

    await graph.run(request)

    assert turn_store.deleted_attempts == [
        AttemptRef(
            scope_digest=scope_digest(request.run_scope),
            attempt_id=ATTEMPT_ID,
        )
    ]


@pytest.mark.asyncio
async def test_cleanup_failure_resumes_finalize_without_repeating_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = retail_request(registry, "safe aggregate")
    gateway = FakeModel(
        [
            model_response(
                request.run_scope,
                sequence=0,
                output=FinalOutput(type="final", content="done"),
            )
        ]
    )
    turn_store = FailOnceCleanupStore()
    graph = RetailGraph(
        context_builder=context_builder(registry),
        profile=registry.get("retail"),
        gateway=gateway,
        dispatcher=None,
        checkpointer=create_memory_saver(),
        turn_store=turn_store,
    )

    with pytest.raises(CheckpointInfrastructureError) as caught:
        await graph.run(request)
    assert caught.value.reason_code == "private_turn_cleanup_failed"
    assert "PRIVATE_CLEANUP_SENTINEL" not in str(caught.value)

    outcome = await graph.run(request)

    assert outcome.status == "stopped"
    assert len(gateway.requests) == 1
    assert turn_store.calls == 2
    assert len(turn_store.deleted_attempts) == 1


@pytest.mark.asyncio
async def test_graph_rejects_model_response_from_another_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = retail_request(registry, "safe aggregate")
    response = model_response(
        request.run_scope,
        sequence=0,
        output=FinalOutput(type="final", content="done"),
    )
    response = response.model_copy(
        update={
            "provider_turn_ref": response.provider_turn_ref.model_copy(
                update={
                    "attempt_id": UUID(
                        "00000000-0000-0000-0000-000000000899"
                    )
                }
            )
        }
    )
    turn_store = RecordingTurnStore()
    graph = RetailGraph(
        context_builder=context_builder(registry),
        profile=registry.get("retail"),
        gateway=FakeModel([response]),
        dispatcher=None,
        checkpointer=create_memory_saver(),
        turn_store=turn_store,
    )

    with pytest.raises(ToolContractError) as caught:
        await graph.run(request)

    assert caught.value.reason_code == "model_response_binding_mismatch"
    assert turn_store.deleted_attempts == []


def test_retail_graph_constructor_rejects_bird_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(CONFIG_ROOT)

    with pytest.raises(ToolContractError) as caught:
        RetailGraph(
            context_builder=context_builder(registry),
            profile=registry.get("bird_a"),  # type: ignore[arg-type]
            gateway=FakeModel([]),
            dispatcher=None,
            checkpointer=create_memory_saver(),
            turn_store=RecordingTurnStore(),
        )

    assert caught.value.reason_code == "retail_profile_required"


@pytest.mark.asyncio
async def test_resume_rejects_changed_request_before_another_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = retail_request(registry, "original question")
    gateway = FakeModel(
        [
            model_response(
                request.run_scope,
                sequence=0,
                output=FinalOutput(type="final", content="done"),
            )
        ]
    )
    graph = RetailGraph(
        context_builder=context_builder(registry),
        profile=registry.get("retail"),
        gateway=gateway,
        dispatcher=None,
        checkpointer=create_memory_saver(),
        turn_store=RecordingTurnStore(),
    )
    await graph.run(request)

    changed = retail_request(registry, "changed question")
    with pytest.raises(CheckpointIncompatible) as caught:
        await graph.run(changed)

    assert caught.value.reason_code == "request_mismatch"
    assert len(gateway.requests) == 1
