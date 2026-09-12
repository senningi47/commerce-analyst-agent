import json
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

import pytest

from commerce_agent.context_builder.contracts import PromptStep
from commerce_agent.knowledge._store import StoredKnowledgeCatalog
from commerce_agent.knowledge.contracts import KnowledgeEvidence, KnowledgeKind
from commerce_agent.knowledge.module import KnowledgeModule
from commerce_agent.model.contracts import RunScope, ToolCall, ToolResult
from commerce_agent.operations.commands import CreateInvestigationTask
from commerce_agent.operations.contracts import ActorContext, ActorRole, EvidenceRef
from commerce_agent.orchestration.tools import (
    BirdToolPort,
    RetailToolDispatcher,
    ToolContractError,
    ToolInfrastructureError,
)
from commerce_agent.query_engine._ast_policy import AstPolicy, ValidatedQuery
from commerce_agent.query_engine.contracts import ExplainSummary, QueryResult
from commerce_agent.query_engine.engine import QueryEngine
from commerce_agent.query_engine.errors import QueryInfrastructureError
from commerce_agent.value_resolver._store import (
    IdLookup,
    StoredRevision,
    StoredValue,
    TextDomainSnapshot,
)
from commerce_agent.value_resolver.contracts import ValueDomain
from commerce_agent.value_resolver.resolver import BusinessValueResolver

ATTEMPT_ID = UUID("00000000-0000-0000-0000-000000000802")


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
                    source_path="tests/unit/orchestration/test_tools.py",
                    source_sha256="c" * 64,
                ),
            ),
        )


class OversizedKnowledgeStore(InMemoryKnowledgeStore):
    async def load_retail_catalog(self) -> StoredKnowledgeCatalog:
        catalog = await super().load_retail_catalog()
        evidence = catalog.evidence[0].model_copy(
            update={"content": {"status": "clarification_required", "text": "x" * 70_000}}
        )
        return StoredKnowledgeCatalog(
            revision=catalog.revision,
            content_sha256=catalog.content_sha256,
            evidence=(evidence,),
        )


class InMemoryValueStore:
    def __init__(
        self,
        *,
        text: Mapping[ValueDomain, tuple[StoredValue, ...]] | None = None,
    ) -> None:
        self.text = text or {}
        self.revision = StoredRevision(
            data_manifest_sha256="d" * 64,
            catalog_revision="retail-catalog-v1",
        )

    async def load_revision(self) -> StoredRevision:
        return self.revision

    async def load_text_domain(self, domain: ValueDomain) -> TextDomainSnapshot:
        return TextDomainSnapshot(
            values=self.text.get(domain, ()),
            aliases=(),
            revision=self.revision,
        )

    async def lookup_id(
        self,
        domain: ValueDomain,
        value: str,
        *,
        prefix: bool,
        limit: int,
    ) -> IdLookup:
        del domain, value, prefix, limit
        return IdLookup(values=(), exceeded_limit=False, revision=self.revision)


class RecordingExecutor:
    def __init__(self, result: QueryResult) -> None:
        self.result = result
        self.calls: list[ValidatedQuery] = []

    async def execute(self, query: ValidatedQuery) -> QueryResult:
        self.calls.append(query)
        return self.result


class FailingQueryEngine:
    async def execute(self, request: object) -> None:
        del request
        raise QueryInfrastructureError(
            "connection_failed",
            "PRIVATE_DATABASE_SENTINEL",
        )


def retail_scope() -> RunScope:
    return RunScope(
        run_id=UUID("00000000-0000-0000-0000-000000000801"),
        track="retail",
        mode="retail",
        subject_id="retail-demo",
        experiment_id="day3",
        config_hash="a" * 64,
    )


def tool_call(name: str, arguments: dict[str, object]) -> ToolCall:
    return ToolCall(
        call_id="call_1",
        name=name,
        arguments_json=json.dumps(arguments, sort_keys=True, separators=(",", ":")),
    )


@pytest.mark.asyncio
async def test_retrieve_tool_returns_bounded_evidence_through_public_module() -> None:
    dispatcher = RetailToolDispatcher(
        knowledge=KnowledgeModule(InMemoryKnowledgeStore()),
        resolver=None,
        query_engine=None,
    )

    result = await dispatcher.execute(
        retail_scope(),
        ATTEMPT_ID,
        tool_call("retrieve_retail_knowledge", {"question": "item amount"}),
    )

    payload = json.loads(result.content_json)
    assert result.status == "success"
    assert payload["strategy"] == "full_catalog"
    assert payload["catalog_revision"] == "retail-catalog-v1"
    assert len(result.content_json.encode("utf-8")) <= 65_536


@pytest.mark.asyncio
async def test_resolver_tool_preserves_all_public_statuses() -> None:
    dispatcher = RetailToolDispatcher(
        knowledge=None,
        resolver=BusinessValueResolver(
            InMemoryValueStore(
                text={
                    ValueDomain.ORDER_STATUS: (StoredValue("delivered", "Delivered", 10),),
                    ValueDomain.CUSTOMER_CITY: (
                        StoredValue("sao paulo", "Sao Paulo", 20),
                        StoredValue("s\u00e3o paulo", "Sao Paulo", 10),
                    ),
                }
            )
        ),
        query_engine=None,
    )

    cases = (
        ("order_status", "delivered", "resolved"),
        ("customer_city", "SAO PAULO", "ambiguous"),
        ("order_status", "zzzzzz", "not_found"),
        ("order_id", "abcde", "too_broad"),
    )
    for index, (domain, raw_text, expected) in enumerate(cases, start=1):
        call = tool_call(
            "resolve_business_value",
            {"domain": domain, "raw_text": raw_text},
        ).model_copy(update={"call_id": f"call_{index}"})
        result = await dispatcher.execute(retail_scope(), ATTEMPT_ID, call)
        payload = json.loads(result.content_json)
        assert result.status == "success"
        assert payload["status"] == expected
        assert all(ref.startswith("value:") for ref in result.source_refs)


@pytest.mark.asyncio
async def test_sql_tool_preserves_explain_and_truncation_fields() -> None:
    executor = RecordingExecutor(
        QueryResult(
            columns=["order_count"],
            rows=[{"order_count": 99_441}],
            explain=ExplainSummary(total_cost=12.5, plan_rows=1),
            truncated=False,
        )
    )
    dispatcher = RetailToolDispatcher(
        knowledge=None,
        resolver=None,
        query_engine=QueryEngine(policy=AstPolicy(), executor=executor),
    )

    result = await dispatcher.execute(
        retail_scope(),
        ATTEMPT_ID,
        tool_call(
            "execute_readonly_sql",
            {"sql": "SELECT COUNT(*) AS order_count FROM retail.orders"},
        ),
    )

    payload = json.loads(result.content_json)
    assert result.status == "success"
    assert payload == {
        "columns": ["order_count"],
        "explain": {"plan_rows": 1, "total_cost": 12.5},
        "row_count": 1,
        "rows": [{"order_count": 99_441}],
        "truncated": False,
    }
    assert len(executor.calls) == 1


class NeverKnowledge:
    def __init__(self) -> None:
        self.called = False

    async def retrieve(self, request: object) -> None:
        del request
        self.called = True
        raise AssertionError("Product seam must not be called")


@pytest.mark.asyncio
async def test_invalid_dispatch_inputs_fail_before_product_call() -> None:
    knowledge = NeverKnowledge()
    dispatcher = RetailToolDispatcher(
        knowledge=knowledge,  # type: ignore[arg-type]
        resolver=None,
        query_engine=None,
    )
    calls = (
        (tool_call("unregistered_tool", {}), "unknown_tool"),
        (
            tool_call(
                "retrieve_retail_knowledge",
                {"question": "safe", "unexpected": "PRIVATE_SENTINEL"},
            ),
            "invalid_tool_arguments",
        ),
        (
            ToolCall.model_construct(
                call_id="call_broken",
                name="retrieve_retail_knowledge",
                arguments_json='{\"question\":\"PRIVATE_SENTINEL\"',
            ),
            "malformed_arguments_json",
        ),
    )

    for call, reason_code in calls:
        with pytest.raises(ToolContractError) as caught:
            await dispatcher.execute(retail_scope(), ATTEMPT_ID, call)
        assert caught.value.reason_code == reason_code
        assert "PRIVATE_SENTINEL" not in str(caught.value)
    assert knowledge.called is False


@pytest.mark.asyncio
async def test_sql_tool_rejects_sensitive_identifier_or_review_columns() -> None:
    columns = [
        "order_id",
        "customer_id",
        "customer_unique_id",
        "seller_id",
        "review_id",
        "review_comment_title",
        "review_comment_message",
        "joined_order_id",
        "linked_customer_id",
        "linked_seller_id",
    ]
    dispatcher = RetailToolDispatcher(
        knowledge=None,
        resolver=None,
        query_engine=QueryEngine(
            policy=AstPolicy(),
            executor=RecordingExecutor(
                QueryResult(
                    columns=columns,
                    rows=[dict.fromkeys(columns, "PRIVATE_SENTINEL")],
                )
            ),
        ),
    )

    with pytest.raises(ToolContractError) as caught:
        await dispatcher.execute(
            retail_scope(),
            ATTEMPT_ID,
            tool_call(
                "execute_readonly_sql",
                {"sql": "SELECT COUNT(*) AS safe_fixture FROM retail.orders"},
            ),
        )

    assert caught.value.reason_code == "sensitive_result_column"
    assert "PRIVATE_SENTINEL" not in str(caught.value)


@pytest.mark.asyncio
async def test_sql_policy_failure_becomes_a_sanitized_tool_result() -> None:
    executor = RecordingExecutor(QueryResult(columns=[], rows=[]))
    dispatcher = RetailToolDispatcher(
        knowledge=None,
        resolver=None,
        query_engine=QueryEngine(policy=AstPolicy(), executor=executor),
    )

    result = await dispatcher.execute(
        retail_scope(),
        ATTEMPT_ID,
        tool_call(
            "execute_readonly_sql",
            {"sql": "UPDATE retail.orders SET order_status = 'x'"},
        ),
    )

    assert result.status == "error"
    assert result.error_class == "SqlPolicyViolation"
    assert json.loads(result.content_json) == {
        "reason_code": "root_not_select",
        "status": "error",
    }
    assert executor.calls == []


@pytest.mark.asyncio
async def test_tool_output_limit_and_infrastructure_errors_are_sanitized() -> None:
    oversized = RetailToolDispatcher(
        knowledge=KnowledgeModule(OversizedKnowledgeStore()),
        resolver=None,
        query_engine=None,
    )
    with pytest.raises(ToolContractError) as too_large:
        await oversized.execute(
            retail_scope(),
            ATTEMPT_ID,
            tool_call("retrieve_retail_knowledge", {"question": "schema"}),
        )
    assert too_large.value.reason_code == "tool_result_too_large"

    unavailable = RetailToolDispatcher(
        knowledge=None,
        resolver=None,
        query_engine=FailingQueryEngine(),  # type: ignore[arg-type]
    )
    with pytest.raises(ToolInfrastructureError) as infrastructure:
        await unavailable.execute(
            retail_scope(),
            ATTEMPT_ID,
            tool_call("execute_readonly_sql", {"sql": "SELECT 1 AS value"}),
        )
    assert infrastructure.value.reason_code == "connection_failed"
    assert "PRIVATE_DATABASE_SENTINEL" not in str(infrastructure.value)


class RecordingWorkflow:
    def __init__(self) -> None:
        self.propose_calls = []

    async def propose(self, request):
        self.propose_calls.append(request)
        raise AssertionError("invalid model arguments must fail before workflow")


@pytest.mark.asyncio
async def test_propose_tool_uses_trusted_actor_not_model_arguments() -> None:
    trusted_actor = ActorContext(
        actor_id=UUID(int=1),
        role="analyst",
        authentication_ref="session:verified:1",
        authenticated_at=datetime(2026, 9, 6, 4, 0, tzinfo=UTC),
    )
    evidence = EvidenceRef(kind="query", ref="query:evidence-1", digest="a" * 64)
    command = CreateInvestigationTask(
        type="create_investigation_task",
        title="Review delayed deliveries",
        priority="high",
        public_summary="Validate the affected cohort.",
        evidence_refs=(evidence,),
        expected_target_version=0,
    )
    workflow = RecordingWorkflow()
    dispatcher = RetailToolDispatcher(
        knowledge=None,
        resolver=None,
        query_engine=None,
        actor=trusted_actor,
        workflow=workflow,
    )
    arguments = {
        "command": command.model_dump(mode="json"),
        "evidence_refs": [evidence.model_dump(mode="json")],
        "idempotency_key": "proposal-key-1",
        "actor": trusted_actor.model_copy(update={"role": ActorRole.APPROVER}).model_dump(
            mode="json"
        ),
    }

    with pytest.raises(ToolContractError) as caught:
        await dispatcher.execute(
            retail_scope(),
            ATTEMPT_ID,
            tool_call("propose_operation", arguments),
            step=PromptStep.RETAIL_REPORT,
        )

    assert caught.value.reason_code == "invalid_tool_arguments"
    assert workflow.propose_calls == []


@pytest.mark.asyncio
async def test_catalog_tool_is_rejected_in_the_wrong_step() -> None:
    dispatcher = RetailToolDispatcher(
        knowledge=None, resolver=None, query_engine=None
    )

    with pytest.raises(ToolContractError) as caught:
        await dispatcher.execute(
            retail_scope(),
            ATTEMPT_ID,
            tool_call("submit_sql_candidate", {}),
            step=PromptStep.RETAIL_REPORT,
        )

    assert caught.value.reason_code == "tool_not_allowed_for_step"


def test_bird_tool_port_protocol_is_structural() -> None:
    class _IndependentPort:
        """Duck-typed port with no relationship to SyntheticBirdAToolPort."""

        async def execute(self, call: ToolCall) -> ToolResult:
            del call
            raise AssertionError("no dispatch expected in this test")

    port: BirdToolPort = _IndependentPort()
    assert isinstance(port, BirdToolPort)
