import os
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import SecretStr

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
)
from commerce_agent.model.fake import FakeModel
from commerce_agent.orchestration._checkpoint import (
    CheckpointIncompatible,
    CheckpointInfrastructureError,
    derive_thread_id,
    open_postgres_saver,
)
from commerce_agent.orchestration.contracts import RetailRunRequest
from commerce_agent.orchestration.retail_graph import RetailGraph
from commerce_agent.orchestration.tools import RetailToolDispatcher, ToolInfrastructureError

pytestmark = pytest.mark.postgres

CONFIG_ROOT = Path(__file__).parents[3] / "configs" / "model"
NOW = datetime(2026, 9, 6, 2, 0, tzinfo=UTC)


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


class FailOnceTurnStore(RecordingTurnStore):
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
                    source_path="tests/integration/checkpoint/test_retail_postgres_resume.py",
                    source_sha256="c" * 64,
                ),
            ),
        )


class FailOnceReadonlyDispatcher:
    def __init__(self, wrapped: RetailToolDispatcher) -> None:
        self._wrapped = wrapped
        self._failed = False
        self.execution_fingerprints: list[str] = []

    async def execute(
        self,
        scope: RunScope,
        attempt_id: UUID,
        call: ToolCall,
    ):
        fingerprint = sha256(
            (
                scope.model_dump_json() + str(attempt_id) + call.model_dump_json()
            ).encode("utf-8")
        ).hexdigest()
        self.execution_fingerprints.append(fingerprint)
        if not self._failed:
            self._failed = True
            raise ToolInfrastructureError("fixture_transient_failure")
        return await self._wrapped.execute(scope, attempt_id, call)


class RecordingDispatcher:
    def __init__(self, wrapped: RetailToolDispatcher) -> None:
        self._wrapped = wrapped
        self.calls = 0

    async def execute(
        self,
        scope: RunScope,
        attempt_id: UUID,
        call: ToolCall,
    ):
        self.calls += 1
        return await self._wrapped.execute(scope, attempt_id, call)


def make_request(
    registry: ProfileRegistry,
    *,
    attempt_id: UUID,
    subject_id: str,
) -> RetailRunRequest:
    profile = registry.get("retail")
    inference = next(
        rule.inference for rule in profile.inference_rules if rule.step == "retail_decide"
    )
    scope = RunScope(
        run_id=UUID(int=attempt_id.int + 10_000),
        track="retail",
        mode="retail",
        subject_id=subject_id,
        experiment_id="day3-task11",
        config_hash=compute_config_hash(profile, inference, "deepseek-chat"),
    )
    content = "What is GMV?"
    return RetailRunRequest(
        run_scope=scope,
        attempt_id=attempt_id,
        current_input=ContextDatum(
            kind="user_input",
            namespace="user_input",
            source_ref="request:current",
            revision=None,
            content=content,
            digest=sha256(content.encode("utf-8")).hexdigest(),
        ),
    )


def make_response(
    request: RetailRunRequest,
    *,
    sequence: int,
    output: FinalOutput | ToolCallOutput,
    finish_reason: str,
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
    expected_ids = (
        tuple(call.call_id for call in output.tool_calls)
        if isinstance(output, ToolCallOutput)
        else ()
    )
    return ModelResponse(
        output=output,
        provider_turn_ref=ProviderTurnRef(
            turn_id=UUID(int=request.attempt_id.int + sequence + 1),
            scope_digest=scope_digest(request.run_scope),
            attempt_id=request.attempt_id,
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
        attempts=(
            ModelAttemptSummary(
                attempt_number=1,
                sent_at=NOW,
                completed_at=NOW,
                outcome="success",
                http_status=200,
                retryable=False,
                charge_ambiguous=False,
                usage=usage,
                cost=cost,
            ),
        ),
    )


def make_gateway(request: RetailRunRequest, *, uses_tool: bool) -> FakeModel:
    final = make_response(
        request,
        sequence=1 if uses_tool else 0,
        output=FinalOutput(type="final", content="GMV requires clarification."),
        finish_reason="stop",
    )
    if not uses_tool:
        return FakeModel([final])
    call = ToolCall(
        call_id="call_1",
        name="retrieve_retail_knowledge",
        arguments_json='{"question":"GMV"}',
    )
    return FakeModel(
        [
            make_response(
                request,
                sequence=0,
                output=ToolCallOutput(type="tool_calls", tool_calls=(call,)),
                finish_reason="tool_calls",
            ),
            final,
        ]
    )


def make_graph(
    registry: ProfileRegistry,
    gateway: FakeModel,
    saver: object,
    *,
    interrupt_after: tuple[str, ...] | None = None,
    turn_store: RecordingTurnStore | None = None,
    dispatcher: (
        RetailToolDispatcher | FailOnceReadonlyDispatcher | RecordingDispatcher | None
    ) = None,
) -> RetailGraph:
    return RetailGraph(
        context_builder=ContextBuilder(
            registry=registry,
            estimator=FixedEstimator(),
            requested_model="deepseek-chat",
            timeout_seconds=Decimal(30),
            provider_user_id="a" * 32,
        ),
        profile=registry.get("retail"),
        gateway=gateway,
        dispatcher=dispatcher
        or RetailToolDispatcher(
            knowledge=KnowledgeModule(InMemoryKnowledgeStore()),
            resolver=None,
            query_engine=None,
        ),
        checkpointer=saver,
        turn_store=turn_store or RecordingTurnStore(),
        _interrupt_after=interrupt_after,
    )


@pytest.mark.parametrize(
    ("node", "uses_tool", "expected_model_calls", "expected_tool_calls"),
    [
        ("prepare_context", False, 1, 0),
        ("call_model", False, 1, 0),
        ("route_output", False, 1, 0),
        ("validate_tools", True, 2, 1),
        ("execute_tools", True, 2, 1),
        ("close_exchange", True, 2, 1),
    ],
)
@pytest.mark.asyncio
async def test_each_retail_node_resumes_to_the_uninterrupted_outcome(
    node: str,
    uses_tool: bool,
    expected_model_calls: int,
    expected_tool_calls: int,
) -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    case_number = [
        "prepare_context",
        "call_model",
        "route_output",
        "validate_tools",
        "execute_tools",
        "close_exchange",
    ].index(node)
    request = make_request(
        registry,
        attempt_id=UUID(int=11_000 + case_number),
        subject_id=f"resume-{node}",
    )
    interrupted_thread = derive_thread_id(request.run_scope, request.attempt_id)
    expected_next_node = {
        "prepare_context": "call_model",
        "call_model": "route_output",
        "route_output": "finalize",
        "validate_tools": "execute_tools",
        "execute_tools": "close_exchange",
        "close_exchange": "prepare_context",
    }[node]
    async with open_postgres_saver(
        SecretStr(os.environ["PRODUCT_CHECKPOINT_DATABASE_DSN"])
    ) as saver:
        try:
            gateway = make_gateway(request, uses_tool=uses_tool)
            interrupted = await make_graph(
                registry,
                gateway,
                saver,
                interrupt_after=(node,),
            ).run(request)
            assert interrupted.status == "stopped"
            assert interrupted.stop is not None
            assert interrupted.stop.reason_code == "checkpoint_interrupt"

            checkpoint = await saver.aget(
                {"configurable": {"thread_id": interrupted_thread}}
            )
            assert checkpoint is not None
            state = checkpoint["channel_values"]
            assert state["state_schema_revision"] == "retail-state-v2"
            assert state["node_revision"] == "retail-nodes-v2"
            assert state["current_node"] == expected_next_node

            resumed = await make_graph(registry, gateway, saver).run(request)
            await saver.adelete_thread(interrupted_thread)
            reference = await make_graph(
                registry,
                make_gateway(request, uses_tool=uses_tool),
                saver,
            ).run(request)

            assert resumed.model_calls == expected_model_calls
            assert resumed.tool_calls == expected_tool_calls
            assert resumed == reference
        finally:
            await saver.adelete_thread(interrupted_thread)


@pytest.mark.asyncio
async def test_finalize_retries_cleanup_without_repeating_model_call() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = make_request(
        registry,
        attempt_id=UUID(int=13_000),
        subject_id="finalize-retry",
    )
    thread_id = derive_thread_id(request.run_scope, request.attempt_id)
    gateway = make_gateway(request, uses_tool=False)
    turn_store = FailOnceTurnStore()

    async with open_postgres_saver(
        SecretStr(os.environ["PRODUCT_CHECKPOINT_DATABASE_DSN"])
    ) as saver:
        try:
            with pytest.raises(CheckpointInfrastructureError) as caught:
                await make_graph(
                    registry,
                    gateway,
                    saver,
                    turn_store=turn_store,
                ).run(request)
            assert caught.value.reason_code == "private_turn_cleanup_failed"
            assert len(gateway.requests) == 1

            checkpoint = await saver.aget({"configurable": {"thread_id": thread_id}})
            assert checkpoint is not None
            state = checkpoint["channel_values"]
            assert state["final_output_json"] is not None
            assert state["cleanup_complete"] is False

            resumed = await make_graph(
                registry,
                gateway,
                saver,
                turn_store=turn_store,
            ).run(request)
            assert resumed.status == "stopped"
            assert resumed.stop is not None
            assert resumed.stop.reason_code == "typed_retail_terminal_required"
            assert len(gateway.requests) == 1
            assert turn_store.calls == 2
        finally:
            await saver.adelete_thread(thread_id)


@pytest.mark.asyncio
async def test_postgres_pending_batch_resumes_at_tool_node() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = make_request(
        registry,
        attempt_id=UUID(int=14_000),
        subject_id="pending-tool-batch",
    )
    thread_id = derive_thread_id(request.run_scope, request.attempt_id)
    gateway = make_gateway(request, uses_tool=True)

    async with open_postgres_saver(
        SecretStr(os.environ["PRODUCT_CHECKPOINT_DATABASE_DSN"])
    ) as saver:
        try:
            interrupted = await make_graph(
                registry,
                gateway,
                saver,
                interrupt_after=("call_model",),
            ).run(request)
            assert interrupted.status == "stopped"
            assert interrupted.stop is not None
            assert interrupted.stop.reason_code == "checkpoint_interrupt"
            assert len(gateway.requests) == 1

            checkpoint = await saver.aget({"configurable": {"thread_id": thread_id}})
            assert checkpoint is not None
            state = checkpoint["channel_values"]
            assert state["pending_tool_batch_json"] is not None
            assert state["model_call_count"] == 1
            assert state["tool_call_count"] == 0

            resumed = await make_graph(registry, gateway, saver).run(request)
            assert resumed.status == "stopped"
            assert resumed.stop is not None
            assert resumed.stop.reason_code == "typed_retail_terminal_required"
            assert len(gateway.requests) == 2
            assert resumed.model_calls == 2
            assert resumed.tool_calls == 1
        finally:
            await saver.adelete_thread(thread_id)


@pytest.mark.asyncio
async def test_readonly_tool_crash_retries_the_same_execution_fingerprint() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = make_request(
        registry,
        attempt_id=UUID(int=15_000),
        subject_id="readonly-tool-retry",
    )
    thread_id = derive_thread_id(request.run_scope, request.attempt_id)
    gateway = make_gateway(request, uses_tool=True)
    dispatcher = FailOnceReadonlyDispatcher(
        RetailToolDispatcher(
            knowledge=KnowledgeModule(InMemoryKnowledgeStore()),
            resolver=None,
            query_engine=None,
        )
    )

    async with open_postgres_saver(
        SecretStr(os.environ["PRODUCT_CHECKPOINT_DATABASE_DSN"])
    ) as saver:
        try:
            with pytest.raises(ToolInfrastructureError):
                await make_graph(
                    registry,
                    gateway,
                    saver,
                    dispatcher=dispatcher,
                ).run(request)
            assert len(gateway.requests) == 1

            resumed = await make_graph(
                registry,
                gateway,
                saver,
                dispatcher=dispatcher,
            ).run(request)
            assert resumed.status == "stopped"
            assert resumed.stop is not None
            assert resumed.stop.reason_code == "typed_retail_terminal_required"
            assert len(gateway.requests) == 2
            assert resumed.model_calls == 2
            assert resumed.tool_calls == 1
            assert len(dispatcher.execution_fingerprints) == 2
            assert (
                dispatcher.execution_fingerprints[0]
                == dispatcher.execution_fingerprints[1]
            )
        finally:
            await saver.adelete_thread(thread_id)


@pytest.mark.asyncio
async def test_closed_exchange_resume_does_not_repeat_the_tool_result() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = make_request(
        registry,
        attempt_id=UUID(int=16_000),
        subject_id="closed-exchange",
    )
    thread_id = derive_thread_id(request.run_scope, request.attempt_id)
    gateway = make_gateway(request, uses_tool=True)
    dispatcher = RecordingDispatcher(
        RetailToolDispatcher(
            knowledge=KnowledgeModule(InMemoryKnowledgeStore()),
            resolver=None,
            query_engine=None,
        )
    )

    async with open_postgres_saver(
        SecretStr(os.environ["PRODUCT_CHECKPOINT_DATABASE_DSN"])
    ) as saver:
        try:
            interrupted = await make_graph(
                registry,
                gateway,
                saver,
                interrupt_after=("close_exchange",),
                dispatcher=dispatcher,
            ).run(request)
            assert interrupted.status == "stopped"
            assert dispatcher.calls == 1
            assert len(gateway.requests) == 1

            checkpoint = await saver.aget({"configurable": {"thread_id": thread_id}})
            assert checkpoint is not None
            state = checkpoint["channel_values"]
            assert state["pending_tool_batch_json"] is None
            assert state["tool_call_count"] == 1

            resumed = await make_graph(
                registry,
                gateway,
                saver,
                dispatcher=dispatcher,
            ).run(request)
            assert resumed.status == "stopped"
            assert resumed.stop is not None
            assert resumed.stop.reason_code == "typed_retail_terminal_required"
            assert dispatcher.calls == 1
            assert len(gateway.requests) == 2
            assert resumed.tool_calls == 1
        finally:
            await saver.adelete_thread(thread_id)


@pytest.mark.asyncio
async def test_postgres_resume_preserves_all_nonzero_budget_counters() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = make_request(
        registry,
        attempt_id=UUID(int=17_000),
        subject_id="preserved-budgets",
    )
    thread_id = derive_thread_id(request.run_scope, request.attempt_id)
    config = {"configurable": {"thread_id": thread_id}}
    gateway = make_gateway(request, uses_tool=False)

    async with open_postgres_saver(
        SecretStr(os.environ["PRODUCT_CHECKPOINT_DATABASE_DSN"])
    ) as saver:
        try:
            interrupted_graph = make_graph(
                registry,
                gateway,
                saver,
                interrupt_after=("prepare_context",),
            )
            interrupted = await interrupted_graph.run(request)
            assert interrupted.status == "stopped"
            await interrupted_graph._graph.aupdate_state(
                config,
                {
                    "clarification_count": 1,
                    "replan_count": 2,
                    "repair_count": 1,
                    "model_call_count": 3,
                    "tool_call_count": 2,
                },
                as_node="prepare_context",
            )

            resumed = await make_graph(registry, gateway, saver).run(request)
            assert resumed.status == "stopped"
            assert resumed.stop is not None
            assert resumed.stop.reason_code == "typed_retail_terminal_required"
            assert resumed.model_calls == 4
            assert resumed.tool_calls == 2

            checkpoint = await saver.aget(config)
            assert checkpoint is not None
            state = checkpoint["channel_values"]
            assert state["clarification_count"] == 1
            assert state["replan_count"] == 2
            assert state["repair_count"] == 1
        finally:
            await saver.adelete_thread(thread_id)


@pytest.mark.parametrize(
    ("field_name", "reason_code"),
    [
        ("run_scope_json", "run_scope_mismatch"),
        ("attempt_id", "attempt_id_mismatch"),
        ("state_schema_revision", "state_schema_mismatch"),
        ("node_revision", "node_revision_mismatch"),
        ("config_hash", "config_hash_mismatch"),
    ],
)
@pytest.mark.asyncio
async def test_postgres_resume_rejects_each_incompatible_binding_before_work(
    field_name: str,
    reason_code: str,
) -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    case_number = [
        "run_scope_json",
        "attempt_id",
        "state_schema_revision",
        "node_revision",
        "config_hash",
    ].index(field_name)
    request = make_request(
        registry,
        attempt_id=UUID(int=18_000 + case_number),
        subject_id=f"incompatible-{field_name}",
    )
    thread_id = derive_thread_id(request.run_scope, request.attempt_id)
    config = {"configurable": {"thread_id": thread_id}}
    gateway = make_gateway(request, uses_tool=False)
    invalid_values = {
        "run_scope_json": request.run_scope.model_copy(
            update={"subject_id": "different-subject"}
        ).model_dump_json(),
        "attempt_id": str(UUID(int=99_999)),
        "state_schema_revision": "retail-state-v1",
        "node_revision": "retail-nodes-v1",
        "config_hash": "f" * 64,
    }

    async with open_postgres_saver(
        SecretStr(os.environ["PRODUCT_CHECKPOINT_DATABASE_DSN"])
    ) as saver:
        try:
            interrupted_graph = make_graph(
                registry,
                gateway,
                saver,
                interrupt_after=("prepare_context",),
            )
            await interrupted_graph.run(request)
            await interrupted_graph._graph.aupdate_state(
                config,
                {field_name: invalid_values[field_name]},
                as_node="prepare_context",
            )

            with pytest.raises(CheckpointIncompatible) as caught:
                await make_graph(registry, gateway, saver).run(request)
            assert caught.value.reason_code == reason_code
            assert len(gateway.requests) == 0
        finally:
            await saver.adelete_thread(thread_id)


@pytest.mark.asyncio
async def test_completed_postgres_thread_returns_saved_terminal_outcome() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = make_request(
        registry,
        attempt_id=UUID(int=19_000),
        subject_id="completed-thread",
    )
    thread_id = derive_thread_id(request.run_scope, request.attempt_id)

    async with open_postgres_saver(
        SecretStr(os.environ["PRODUCT_CHECKPOINT_DATABASE_DSN"])
    ) as saver:
        try:
            first_gateway = make_gateway(request, uses_tool=False)
            first = await make_graph(registry, first_gateway, saver).run(request)
            assert first.status == "stopped"
            assert first.stop is not None
            assert first.stop.reason_code == "typed_retail_terminal_required"
            assert len(first_gateway.requests) == 1

            no_more_responses = FakeModel([])
            restored = await make_graph(
                registry,
                no_more_responses,
                saver,
            ).run(request)
            assert restored == first
            assert len(no_more_responses.requests) == 0
        finally:
            await saver.adelete_thread(thread_id)
