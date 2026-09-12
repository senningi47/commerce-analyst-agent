import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from commerce_agent.context_builder.contracts import ContextDatum
from commerce_agent.context_builder.profiles import ProfileRegistry
from commerce_agent.model.contracts import ToolCall, ToolCallOutput
from commerce_agent.model.fake import FakeModel
from commerce_agent.operations.contracts import (
    ActorContext,
    ActorRole,
    CommandPreview,
    ProposalRef,
    ProposalSnapshot,
    ProposalStatus,
)
from commerce_agent.orchestration._checkpoint import create_memory_saver
from commerce_agent.orchestration.retail_graph import RetailGraph
from commerce_agent.orchestration.tools import RetailToolDispatcher
from commerce_agent.query_engine.contracts import QueryResult
from commerce_agent.query_engine.errors import SqlExecutionError
from commerce_agent.sql_reasoning.contracts import SqlCandidate
from commerce_agent.sql_reasoning.reasoner import fingerprints
from tests.unit.orchestration.test_retail_graph import (
    FailOnceCleanupStore,
    RecordingTurnStore,
    context_builder,
    model_response,
    retail_request,
)

NOW = datetime(2026, 9, 6, 4, 0, tzinfo=UTC)


def _tool_call(name: str, call_id: str, arguments: dict[str, object]) -> ToolCall:
    return ToolCall(
        call_id=call_id,
        name=name,
        arguments_json=json.dumps(arguments, sort_keys=True, separators=(",", ":")),
    )


class ScriptedSqlReasoner:
    def __init__(self) -> None:
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        sql = (
            "SELECT COUNT(*) AS order_count FROM retail.orders"
            if request.step_id == "baseline"
            else "SELECT order_status, COUNT(*) AS order_count FROM retail.orders "
            "GROUP BY order_status"
        )
        structural, execution = fingerprints(sql)
        metadata = model_response(
            request.run_scope,
            sequence=request.sequence,
            output=ToolCallOutput(
                type="tool_calls",
                tool_calls=(
                    _tool_call(
                        "submit_sql_candidate",
                        f"sql-{request.step_id}",
                        {
                            "evidence_refs": [request.evidence[0].source_ref],
                            "sql": sql,
                            "step_id": request.step_id,
                            "type": "submit_sql_candidate",
                        },
                    ),
                ),
            ),
            finish_reason="tool_calls",
        )
        return SqlCandidate(
            sql=sql,
            step_id=request.step_id,
            repair_number=request.repair_number,
            evidence_refs=(request.evidence[0].source_ref,),
            structural_fingerprint=structural,
            execution_fingerprint=execution,
            usage=metadata.usage,
            cost=metadata.cost,
            attempts=metadata.attempts,
        )


class ScriptedQueryEngine:
    def __init__(self) -> None:
        self.requests = []

    async def execute(self, request):
        self.requests.append(request)
        if "GROUP BY" in request.sql:
            return QueryResult(
                columns=["order_status", "order_count"],
                rows=[{"order_status": "delivered", "order_count": 7}],
            )
        return QueryResult(columns=["order_count"], rows=[{"order_count": 7}])


class FixedProposalWorkflow:
    def __init__(self) -> None:
        self.requests = []

    async def propose(self, request):
        self.requests.append(request)
        return ProposalSnapshot(
            proposal_ref=ProposalRef(proposal_id=UUID(int=70), version=1),
            status=ProposalStatus.PENDING,
            requester_id=request.actor.actor_id,
            command_type=request.command.type,
            payload_sha256="7" * 64,
            preview=CommandPreview(
                command_type=request.command.type,
                title="Investigate GMV movement",
                public_summary="Create one evidence-bound investigation.",
                target_versions={"investigation_task": 0},
                affected_rows=1,
            ),
            created_at=NOW,
            expires_at=NOW.replace(day=7),
        )


@pytest.mark.asyncio
async def test_missing_hard_slots_returns_one_multi_item_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(Path(__file__).parents[3] / "configs" / "model")
    request = retail_request(registry, "Compare GMV by month.")
    arguments = {
        "conversation_id": request.run_scope.subject_id,
        "items": [
            {
                "allowed_values": ["item_amount", "payment_amount"],
                "question": "Which approved GMV definition should be used?",
                "slot": "gmv_metric_definition",
            },
            {
                "allowed_values": ["order_purchase_timestamp", "order_approved_at"],
                "question": "Which time field should be used?",
                "slot": "time_field",
            },
            {
                "allowed_values": [],
                "question": "What minimum order count should be required?",
                "slot": "minimum_order_count",
            },
        ],
        "request_id": str(UUID(int=10)),
        "type": "clarification_request",
    }
    call = ToolCall(
        call_id="clarify-1",
        name="request_clarification",
        arguments_json=json.dumps(arguments, sort_keys=True, separators=(",", ":")),
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
    graph = RetailGraph(
        context_builder=context_builder(registry),
        profile=registry.get("retail"),
        gateway=gateway,
        dispatcher=None,
        checkpointer=create_memory_saver(),
        turn_store=RecordingTurnStore(),
        operation_workflow=object(),
    )

    outcome = await graph.run(request)

    assert outcome.status == "needs_input"
    assert tuple(item.slot for item in outcome.terminal.items) == (
        "gmv_metric_definition",
        "time_field",
        "minimum_order_count",
    )
    assert outcome.attempt_id == request.attempt_id
    assert len(gateway.requests) == 1


@pytest.mark.asyncio
async def test_same_attempt_terminal_resume_does_not_repeat_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(Path(__file__).parents[3] / "configs" / "model")
    request = retail_request(registry, "Clarify GMV.")
    call = ToolCall(
        call_id="clarify-1",
        name="request_clarification",
        arguments_json=(
            '{"conversation_id":"retail-demo","items":[{"allowed_values":[],"question":'
            '"Which GMV?","slot":"gmv_metric_definition"}],"request_id":'
            '"00000000-0000-0000-0000-000000000010","type":"clarification_request"}'
        ),
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
    graph = RetailGraph(
        context_builder=context_builder(registry),
        profile=registry.get("retail"),
        gateway=gateway,
        dispatcher=None,
        checkpointer=create_memory_saver(),
        turn_store=RecordingTurnStore(),
        operation_workflow=object(),
    )

    first = await graph.run(request)
    resumed = await graph.run(request)

    assert resumed == first
    assert len(gateway.requests) == 1


@pytest.mark.asyncio
async def test_day4_terminal_resume_survives_graph_reconstruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(Path(__file__).parents[3] / "configs" / "model")
    request = retail_request(registry, "Clarify GMV across a reconstructed graph.")
    call = ToolCall(
        call_id="clarify-reconstructed",
        name="request_clarification",
        arguments_json=(
            '{"conversation_id":"retail-demo","items":[{"allowed_values":[],'
            '"question":"Which GMV?","slot":"gmv_metric_definition"}],"request_id":'
            '"00000000-0000-0000-0000-000000000010","type":"clarification_request"}'
        ),
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
    saver = create_memory_saver()

    first = await RetailGraph(
        context_builder=context_builder(registry),
        profile=registry.get("retail"),
        gateway=gateway,
        dispatcher=None,
        checkpointer=saver,
        turn_store=RecordingTurnStore(),
        operation_workflow=object(),
    ).run(request)
    resumed = await RetailGraph(
        context_builder=context_builder(registry),
        profile=registry.get("retail"),
        gateway=gateway,
        dispatcher=None,
        checkpointer=saver,
        turn_store=RecordingTurnStore(),
        operation_workflow=object(),
    ).run(request)

    assert resumed == first
    assert len(gateway.requests) == 1


@pytest.mark.asyncio
async def test_day4_plan_query_reconcile_propose_report_path_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(Path(__file__).parents[3] / "configs" / "model")
    request = retail_request(registry, "Investigate the GMV change.")
    knowledge = ContextDatum(
        kind="knowledge",
        namespace="retail_knowledge",
        source_ref="knowledge:metric.orders:v1",
        revision="v1",
        content="Approved order and GMV definitions.",
        digest=sha256(b"Approved order and GMV definitions.").hexdigest(),
    )
    request = request.model_copy(update={"evidence": (knowledge,)})
    plan = {
        "type": "investigation_plan",
        "conversation_id": request.run_scope.subject_id,
        "plan_id": str(UUID(int=60)),
        "confirmed_facts": {
            "gmv_metric_definition": "item_amount",
            "valid_order_statuses": ["delivered"],
            "time_field": "order_purchase_timestamp",
            "time_range": {
                "started_at": "2026-08-01T00:00:00Z",
                "ended_at": "2026-09-01T00:00:00Z",
            },
            "analysis_grain": "month",
            "minimum_order_count": 5,
            "anomaly_threshold": "0.20",
            "business_value_refs": [],
        },
        "steps": [
            {
                "step_id": "baseline",
                "kind": "baseline",
                "question": "What is the total?",
                "measures": ["order_count"],
                "grains": ["month"],
                "evidence_refs": [knowledge.source_ref],
                "reconciliation_rule_refs": ["rule:total"],
            },
            {
                "step_id": "status_drilldown",
                "kind": "drilldown",
                "question": "Which status contributed?",
                "measures": ["order_count"],
                "grains": ["order_status"],
                "evidence_refs": [knowledge.source_ref],
                "reconciliation_rule_refs": ["rule:total"],
            },
        ],
        "evidence_refs": [knowledge.source_ref],
        "reconciliation_rule_refs": ["rule:total"],
        "stop_conditions": ["all_steps_complete"],
    }
    evidence_ref = {
        "kind": "query",
        "ref": "query:baseline",
        "digest": "a" * 64,
    }
    propose = {
        "command": {
            "type": "create_investigation_task",
            "title": "Investigate GMV movement",
            "priority": "high",
            "public_summary": "Validate the status contribution.",
            "evidence_refs": [evidence_ref],
            "expected_target_version": 0,
        },
        "evidence_refs": [evidence_ref],
        "idempotency_key": "retail-day4-proposal-1",
        "revises": None,
    }
    report = {
        "type": "investigation_report",
        "conversation_id": request.run_scope.subject_id,
        "report_id": str(UUID(int=80)),
        "claims": [
            {
                "claim_id": "status_contribution",
                "text": "Delivered orders account for the observed count.",
                "evidence_refs": ["query:baseline", "query:status_drilldown"],
            }
        ],
        "available_evidence": [
            {
                "evidence_id": "query:baseline",
                "kind": "query",
                "summary": "Baseline query.",
                "digest": "b" * 64,
            },
            {
                "evidence_id": "query:status_drilldown",
                "kind": "query",
                "summary": "Status drilldown.",
                "digest": "c" * 64,
            },
            {
                "evidence_id": "reconciliation:plan",
                "kind": "reconciliation",
                "summary": "Reconciliation passed.",
                "digest": "d" * 64,
            },
        ],
        "limitations": [],
        "reconciliation_refs": ["reconciliation:plan"],
        "proposal_refs": [{"proposal_id": str(UUID(int=70)), "version": 1}],
        "selected_risk_status": None,
        "sensitivity_comparison": None,
    }
    gateway = FakeModel(
        [
            model_response(
                request.run_scope,
                sequence=0,
                output=ToolCallOutput(
                    type="tool_calls",
                    tool_calls=(_tool_call("submit_investigation_plan", "plan-1", plan),),
                ),
                finish_reason="tool_calls",
            ),
            model_response(
                request.run_scope,
                sequence=3,
                output=ToolCallOutput(
                    type="tool_calls",
                    tool_calls=(_tool_call("propose_operation", "proposal-1", propose),),
                ),
                finish_reason="tool_calls",
            ),
            model_response(
                request.run_scope,
                sequence=4,
                output=ToolCallOutput(
                    type="tool_calls",
                    tool_calls=(
                        _tool_call("submit_investigation_report", "report-1", report),
                    ),
                ),
                finish_reason="tool_calls",
            ),
        ]
    )
    reasoner = ScriptedSqlReasoner()
    query = ScriptedQueryEngine()
    workflow = FixedProposalWorkflow()
    actor = ActorContext(
        actor_id=UUID(int=1),
        role=ActorRole.ANALYST,
        authentication_ref="session:verified:1",
        authenticated_at=NOW,
    )
    dispatcher = RetailToolDispatcher(
        knowledge=None,
        resolver=None,
        query_engine=None,
        actor=actor,
        workflow=workflow,
    )
    graph = RetailGraph(
        context_builder=context_builder(registry),
        profile=registry.get("retail"),
        gateway=gateway,
        dispatcher=dispatcher,
        checkpointer=create_memory_saver(),
        turn_store=RecordingTurnStore(),
        operation_workflow=workflow,
        sql_reasoner=reasoner,
        query_engine=query,
    )

    outcome = await graph.run(request)

    assert outcome.status == "completed"
    assert outcome.terminal.proposal_refs == (ProposalRef(proposal_id=UUID(int=70), version=1),)
    assert outcome.model_calls == 5
    assert outcome.tool_calls == 5
    assert len(reasoner.requests) == 2
    assert len(query.requests) == 2
    assert len(workflow.requests) == 1


def _resumable_plan(subject_id: str, evidence_ref: str) -> dict[str, object]:
    return {
        "type": "investigation_plan",
        "conversation_id": subject_id,
        "plan_id": str(UUID(int=160)),
        "confirmed_facts": {
            "gmv_metric_definition": "item_amount",
            "valid_order_statuses": ["delivered"],
            "time_field": "order_purchase_timestamp",
            "time_range": {
                "started_at": "2026-08-01T00:00:00Z",
                "ended_at": "2026-09-01T00:00:00Z",
            },
            "analysis_grain": "month",
            "minimum_order_count": 5,
            "anomaly_threshold": "0.20",
            "business_value_refs": [],
        },
        "steps": [
            {
                "step_id": "baseline",
                "kind": "baseline",
                "question": "What is the total?",
                "measures": ["order_count"],
                "grains": ["month"],
                "evidence_refs": [evidence_ref],
                "reconciliation_rule_refs": ["rule:total"],
            },
            {
                "step_id": "status_drilldown",
                "kind": "drilldown",
                "question": "Which status contributed?",
                "measures": ["order_count"],
                "grains": ["order_status"],
                "evidence_refs": [evidence_ref],
                "reconciliation_rule_refs": ["rule:total"],
            },
        ],
        "evidence_refs": [evidence_ref],
        "reconciliation_rule_refs": ["rule:total"],
        "stop_conditions": ["all_steps_complete"],
    }


def _resumable_report(subject_id: str) -> dict[str, object]:
    return {
        "type": "investigation_report",
        "conversation_id": subject_id,
        "report_id": str(UUID(int=180)),
        "claims": [
            {
                "claim_id": "status_contribution",
                "text": "Delivered orders account for the observed count.",
                "evidence_refs": ["query:baseline", "query:status_drilldown"],
            }
        ],
        "available_evidence": [
            {
                "evidence_id": "query:baseline",
                "kind": "query",
                "summary": "Baseline query.",
                "digest": "b" * 64,
            },
            {
                "evidence_id": "query:status_drilldown",
                "kind": "query",
                "summary": "Status drilldown.",
                "digest": "c" * 64,
            },
            {
                "evidence_id": "reconciliation:plan",
                "kind": "reconciliation",
                "summary": "Reconciliation passed.",
                "digest": "d" * 64,
            },
        ],
        "limitations": [],
        "reconciliation_refs": ["reconciliation:plan"],
        "proposal_refs": [{"proposal_id": str(UUID(int=70)), "version": 1}],
        "selected_risk_status": None,
        "sensitivity_comparison": None,
    }


class AdaptiveRetailGateway:
    def __init__(self, request) -> None:
        self._request = request
        self.requests = []
        self._report_calls = 0

    async def complete(self, model_request):
        self.requests.append(model_request)
        tool_names = {item.name for item in model_request.tools}
        if "submit_investigation_plan" in tool_names:
            call = _tool_call(
                "submit_investigation_plan",
                "adaptive-plan",
                _resumable_plan(
                    self._request.run_scope.subject_id,
                    self._request.evidence[0].source_ref,
                ),
            )
        elif self._report_calls == 0:
            self._report_calls += 1
            evidence_ref = {
                "kind": "query",
                "ref": "query:baseline",
                "digest": "a" * 64,
            }
            call = _tool_call(
                "propose_operation",
                "adaptive-proposal",
                {
                    "command": {
                        "type": "create_investigation_task",
                        "title": "Investigate GMV movement",
                        "priority": "high",
                        "public_summary": "Validate the status contribution.",
                        "evidence_refs": [evidence_ref],
                        "expected_target_version": 0,
                    },
                    "evidence_refs": [evidence_ref],
                    "idempotency_key": "retail-day4-adaptive-proposal",
                    "revises": None,
                },
            )
        else:
            call = _tool_call(
                "submit_investigation_report",
                "adaptive-report",
                _resumable_report(self._request.run_scope.subject_id),
            )
        return model_response(
            model_request.run_scope,
            sequence=model_request.sequence,
            output=ToolCallOutput(type="tool_calls", tool_calls=(call,)),
            finish_reason="tool_calls",
        )


def _resumable_fixture(
    registry,
    request,
    *,
    interrupt_after=(),
    turn_store=None,
    query_engine=None,
):
    gateway = AdaptiveRetailGateway(request)
    reasoner = ScriptedSqlReasoner()
    query = query_engine or ScriptedQueryEngine()
    workflow = FixedProposalWorkflow()
    dispatcher = RetailToolDispatcher(
        knowledge=None,
        resolver=None,
        query_engine=None,
        actor=ActorContext(
            actor_id=UUID(int=1),
            role=ActorRole.ANALYST,
            authentication_ref="session:verified:1",
            authenticated_at=NOW,
        ),
        workflow=workflow,
    )
    graph = RetailGraph(
        context_builder=context_builder(registry),
        profile=registry.get("retail"),
        gateway=gateway,
        dispatcher=dispatcher,
        checkpointer=create_memory_saver(),
        turn_store=turn_store or RecordingTurnStore(),
        operation_workflow=workflow,
        sql_reasoner=reasoner,
        query_engine=query,
        _interrupt_after=interrupt_after,
    )
    return graph, gateway, reasoner, query, workflow


@pytest.mark.parametrize(
    "interrupt_after",
    ["checkpoint_candidate", "execute_query", "create_proposal", "commit_terminal"],
)
@pytest.mark.asyncio
async def test_resume_is_equivalent_and_idempotent(
    monkeypatch: pytest.MonkeyPatch, interrupt_after: str
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(Path(__file__).parents[3] / "configs" / "model")
    request = retail_request(registry, "Investigate resumable GMV evidence.")
    knowledge = ContextDatum(
        kind="knowledge",
        namespace="retail_knowledge",
        source_ref="knowledge:metric.orders:v1",
        revision="v1",
        content="Approved order and GMV definitions.",
        digest=sha256(b"Approved order and GMV definitions.").hexdigest(),
    )
    request = request.model_copy(update={"evidence": (knowledge,)})
    graph, gateway, reasoner, query, workflow = _resumable_fixture(
        registry, request, interrupt_after=(interrupt_after,)
    )

    interrupted = await graph.run(request)
    resumed = await graph.run(request)
    uninterrupted_graph, *_rest = _resumable_fixture(registry, request)
    uninterrupted = await uninterrupted_graph.run(request)

    assert interrupted.status == "stopped"
    assert interrupted.stop.reason_code == "checkpoint_interrupt"
    assert resumed == uninterrupted
    assert len(gateway.requests) == 3
    assert len(reasoner.requests) == 2
    assert len(query.requests) == 2
    assert len({item.sql for item in query.requests}) == 2
    assert len(workflow.requests) == 1


@pytest.mark.asyncio
async def test_day4_cleanup_failure_retries_finalize_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(Path(__file__).parents[3] / "configs" / "model")
    request = retail_request(registry, "Investigate cleanup-safe GMV evidence.")
    knowledge = ContextDatum(
        kind="knowledge",
        namespace="retail_knowledge",
        source_ref="knowledge:metric.orders:v1",
        revision="v1",
        content="Approved order and GMV definitions.",
        digest=sha256(b"Approved order and GMV definitions.").hexdigest(),
    )
    request = request.model_copy(update={"evidence": (knowledge,)})
    turn_store = FailOnceCleanupStore()
    graph, gateway, reasoner, query, workflow = _resumable_fixture(
        registry, request, turn_store=turn_store
    )

    first = await graph.run(request)
    second = await graph.run(request)

    assert first.status == "stopped"
    assert first.stop.reason_code == "private_turn_cleanup_failed"
    assert second.status == "completed"
    assert len(gateway.requests) == 3
    assert len(reasoner.requests) == 2
    assert len(query.requests) == 2
    assert len(workflow.requests) == 1
    assert turn_store.calls == 2


class GloballyFailingQueryEngine(ScriptedQueryEngine):
    def __init__(self) -> None:
        super().__init__()
        self._baseline_failures = 0

    async def execute(self, request):
        self.requests.append(request)
        if "GROUP BY" not in request.sql and self._baseline_failures < 2:
            self._baseline_failures += 1
            raise SqlExecutionError("undefined_column", "fixture execution failed")
        if "GROUP BY" in request.sql:
            raise SqlExecutionError("undefined_column", "fixture execution failed")
        return QueryResult(columns=["order_count"], rows=[{"order_count": 7}])


@pytest.mark.asyncio
async def test_sql_repair_budget_is_global_across_plan_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    registry = ProfileRegistry.load(Path(__file__).parents[3] / "configs" / "model")
    request = retail_request(registry, "Investigate globally budgeted SQL repair.")
    knowledge = ContextDatum(
        kind="knowledge",
        namespace="retail_knowledge",
        source_ref="knowledge:metric.orders:v1",
        revision="v1",
        content="Approved order and GMV definitions.",
        digest=sha256(b"Approved order and GMV definitions.").hexdigest(),
    )
    request = request.model_copy(update={"evidence": (knowledge,)})
    query = GloballyFailingQueryEngine()
    graph, _gateway, reasoner, _query, workflow = _resumable_fixture(
        registry, request, query_engine=query
    )

    outcome = await graph.run(request)

    assert outcome.status == "stopped"
    assert outcome.stop.reason_code == "sql_repair_limit"
    assert len(reasoner.requests) == 5
    assert len(query.requests) == 5
    assert len(workflow.requests) == 0
