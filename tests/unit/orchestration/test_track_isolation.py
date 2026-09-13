import ast
import inspect
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
from commerce_agent.model.fake import FakeModel
from commerce_agent.orchestration._checkpoint import create_memory_saver
from commerce_agent.orchestration.bird_a_graph import BirdAGraph
from commerce_agent.orchestration.bird_c_responder import BirdCResponder
from commerce_agent.orchestration.contracts import (
    BirdARunRequest,
    BirdCRequest,
    RetailRunRequest,
)
from commerce_agent.orchestration.retail_graph import RetailGraph
from commerce_agent.orchestration.tools import SyntheticBirdAToolPort

ROOT = Path(__file__).parents[3]
CONFIG_ROOT = ROOT / "configs" / "model"
ORCHESTRATION_ROOT = ROOT / "src" / "commerce_agent" / "orchestration"
NOW = datetime(2026, 9, 6, 4, 0, tzinfo=UTC)
COMMON_NAMESPACES = {"user_input", "confirmed_fact", "runtime_error"}
SYNTHETIC_MANIFEST = json.loads(
    (ROOT / "tests" / "fixtures" / "orchestration" / "bird-a-runtime.synthetic.v1.json").read_text(
        encoding="utf-8"
    )
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


def imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def scope_and_attempt(
    registry: ProfileRegistry,
    key: str,
    *,
    mode: str,
    identity: int,
) -> tuple[RunScope, UUID]:
    profile = registry.get(key)
    inference = next(rule.inference for rule in profile.inference_rules)
    return (
        RunScope(
            run_id=UUID(int=identity),
            track="retail" if mode == "retail" else "bird",
            mode=mode,
            subject_id=f"synthetic-{mode}",
            experiment_id="day3",
            config_hash=compute_config_hash(profile, inference, "deepseek-chat"),
        ),
        UUID(int=identity + 100),
    )


def datum(content: str, *, kind: str = "user_input", namespace: str = "user_input") -> ContextDatum:
    return ContextDatum(
        kind=kind,
        namespace=namespace,
        source_ref="synthetic:entry",
        revision=None,
        content=content,
        digest=sha256(content.encode("utf-8")).hexdigest(),
    )


def model_response(
    scope: RunScope,
    attempt_id: UUID,
    *,
    sequence: int,
    output: FinalOutput | ToolCallOutput,
    finish_reason: str = "stop",
) -> ModelResponse:
    usage = ReportedUsage(
        status="reported",
        prompt_tokens=2,
        cache_hit_tokens=1,
        cache_miss_tokens=1,
        completion_tokens=1,
        reasoning_tokens=0,
        total_tokens=3,
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
            turn_id=UUID(int=sequence + 1),
            scope_digest=scope_digest(scope),
            attempt_id=attempt_id,
            sequence=sequence,
            payload_sha256=f"{sequence + 1:064x}",
            expected_tool_call_ids=expected_ids,
            token_weight=1,
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


def context_builder(registry: ProfileRegistry, provider_user_id: str) -> ContextBuilder:
    return ContextBuilder(
        registry=registry,
        estimator=FixedEstimator(),
        requested_model="deepseek-chat",
        timeout_seconds=Decimal(30),
        provider_user_id=provider_user_id,
    )


def test_bird_sources_do_not_import_product_or_checkpoint_modules() -> None:
    forbidden = {
        "commerce_agent.knowledge",
        "commerce_agent.operations",
        "commerce_agent.product_eval",
        "commerce_agent.value_resolver",
        "commerce_agent.query_engine",
        "commerce_agent.orchestration._checkpoint",
        "langgraph.checkpoint.postgres",
    }
    for filename in ("bird_a_graph.py", "bird_c_responder.py"):
        source = ORCHESTRATION_ROOT / filename
        imported = imported_modules(ast.parse(source.read_text(encoding="utf-8")))
        assert not any(
            module == blocked or module.startswith(blocked + ".")
            for module in imported
            for blocked in forbidden
        )
    assert "mode" not in inspect.signature(BirdAGraph).parameters
    assert "mode" not in inspect.signature(BirdCResponder).parameters
    assert "checkpointer" not in inspect.signature(BirdAGraph).parameters
    assert "checkpointer" not in inspect.signature(BirdCResponder).parameters
    assert "checkpointer" in inspect.signature(RetailGraph).parameters


def test_bird_profiles_and_constructors_have_no_product_capability() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    forbidden_tool_terms = {
        "proposal",
        "approval",
        "operation",
        "ops",
        "risk",
        "alert",
    }
    for profile_key in ("bird_a", "bird_c"):
        tool_names = {
            name
            for rule in registry.get(profile_key).inference_rules
            for name in rule.tool_names
        }
        assert not any(
            term in name.casefold()
            for name in tool_names
            for term in forbidden_tool_terms
        )
        assert "execute_readonly_sql" not in tool_names

    forbidden_parameters = {
        "actor",
        "actor_context",
        "conversation_state",
        "credential",
        "dsn",
        "operation_store",
        "product_checkpoint",
        "product_store",
    }
    for constructor in (BirdAGraph, BirdCResponder):
        assert not forbidden_parameters & set(inspect.signature(constructor).parameters)

    retail_tools = {
        name
        for rule in registry.get("retail").inference_rules
        for name in rule.tool_names
    }
    assert not any(
        term in name.casefold()
        for name in retail_tools
        for term in ("coin", "simulator", "submit_feedback")
    )


@pytest.mark.asyncio
async def test_three_public_entries_keep_profiles_and_topologies_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(CONFIG_ROOT)
    retail_scope, retail_attempt = scope_and_attempt(
        registry, "retail", mode="retail", identity=1_001
    )
    bird_a_scope, bird_a_attempt = scope_and_attempt(registry, "bird_a", mode="a", identity=1_002)
    bird_c_scope, bird_c_attempt = scope_and_attempt(registry, "bird_c", mode="c", identity=1_003)
    bird_a_call = ToolCall(
        call_id="a_call",
        name="get_schema",
        arguments_json="{}",
    )
    bird_c_call = ToolCall(
        call_id="c_call",
        name="submit_sql",
        arguments_json='{"sql":"SELECT COUNT(*) FROM synthetic_orders"}',
    )
    retail_gateway = FakeModel(
        [
            model_response(
                retail_scope,
                retail_attempt,
                sequence=0,
                output=FinalOutput(type="final", content="retail complete"),
            )
        ]
    )
    bird_a_gateway = FakeModel(
        [
            model_response(
                bird_a_scope,
                bird_a_attempt,
                sequence=0,
                output=ToolCallOutput(type="tool_calls", tool_calls=(bird_a_call,)),
                finish_reason="tool_calls",
            ),
            model_response(
                bird_a_scope,
                bird_a_attempt,
                sequence=1,
                output=FinalOutput(type="final", content="bird a complete"),
            ),
        ]
    )
    bird_c_gateway = FakeModel(
        [
            model_response(
                bird_c_scope,
                bird_c_attempt,
                sequence=0,
                output=ToolCallOutput(type="tool_calls", tool_calls=(bird_c_call,)),
                finish_reason="tool_calls",
            )
        ]
    )

    retail = await RetailGraph(
        context_builder=context_builder(registry, "1" * 32),
        profile=registry.get("retail"),
        gateway=retail_gateway,
        dispatcher=None,
        checkpointer=create_memory_saver(),
        turn_store=RecordingTurnStore(),
    ).run(
        RetailRunRequest(
            run_scope=retail_scope,
            attempt_id=retail_attempt,
            current_input=datum("retail input"),
        )
    )
    bird_a = await BirdAGraph(
        context_builder=context_builder(registry, "2" * 32),
        profile=registry.get("bird_a"),
        gateway=bird_a_gateway,
        tool_port=SyntheticBirdAToolPort.from_fixture(SYNTHETIC_MANIFEST),
        turn_store=RecordingTurnStore(),
    ).run(
        BirdARunRequest(
            run_scope=bird_a_scope,
            attempt_id=bird_a_attempt,
            current_input=datum("bird a input"),
            max_tool_calls=1,
        )
    )
    bird_c = await BirdCResponder(
        context_builder=context_builder(registry, "3" * 32),
        profile=registry.get("bird_c"),
        gateway=bird_c_gateway,
        turn_store=RecordingTurnStore(),
    ).respond(
        BirdCRequest(
            run_scope=bird_c_scope,
            attempt_id=bird_c_attempt,
            current_phase=datum(
                "bird c phase",
                kind="phase",
                namespace="bird_c_phase",
            ),
        )
    )

    assert retail.status == "stopped"
    assert bird_a.status == "completed"
    assert bird_a.model_calls == 2
    assert bird_c.candidate.type == "submit_sql"
    assert len(bird_c_gateway.requests) == 1
    tool_sets = [
        {tool.name for tool in gateway.requests[0].tools}
        for gateway in (retail_gateway, bird_a_gateway, bird_c_gateway)
    ]
    # Track isolation: the product track must share no tools with either BIRD
    # profile. bird_a/bird_c intentionally share the official ask_user and
    # submit_sql tools (official c-interact is a 2-tool subset of a-interact).
    assert tool_sets[0].isdisjoint(tool_sets[1])
    assert tool_sets[0].isdisjoint(tool_sets[2])
    namespace_sets = [
        {namespace.value for namespace in registry.get(key).allowed_namespaces}
        for key in ("retail", "bird_a", "bird_c")
    ]
    assert namespace_sets[0] & namespace_sets[1] == COMMON_NAMESPACES
    assert namespace_sets[0] & namespace_sets[2] == COMMON_NAMESPACES
    assert namespace_sets[1] & namespace_sets[2] == COMMON_NAMESPACES | {
        "bird_schema",
        "bird_official_feedback",
    }
    for key, gateway in zip(
        ("retail", "bird_a", "bird_c"),
        (retail_gateway, bird_a_gateway, bird_c_gateway),
        strict=True,
    ):
        common_policy, profile_policy = registry.policy_for(key)
        assert tuple(message.content for message in gateway.requests[0].messages[:2]) == (
            common_policy,
            profile_policy,
        )
