from datetime import UTC, datetime
from uuid import UUID

import pytest

from commerce_agent.operations.contracts import (
    ActorContext,
    ActorRole,
    DecisionReceipt,
    ExecutionGrant,
    ExecutionReceipt,
    ExecutionStatus,
    ProposalStatus,
)
from commerce_agent.orchestration.contracts import (
    ClarificationItem,
    ClarificationRequest,
    InvestigationClaim,
    InvestigationEvidence,
    InvestigationReport,
    RetailRunOutcome,
)
from commerce_agent.product_eval._reset import InMemoryScenarioReset
from commerce_agent.product_eval.contracts import ProductScenario
from commerce_agent.product_eval.driver import ProductScenarioDriver
from commerce_agent.query_engine.contracts import QueryResult
from tests.unit.product_eval.test_contracts import scenario_payload

NOW = datetime(2026, 9, 6, 4, 0, tzinfo=UTC)


def _outcome(terminal: ClarificationRequest | InvestigationReport) -> RetailRunOutcome:
    return RetailRunOutcome(
        status=(
            "needs_input" if isinstance(terminal, ClarificationRequest) else "completed"
        ),
        terminal=terminal,
        model_calls=1,
        tool_calls=1,
        prompt_policy_hash="1" * 64,
        rendered_prompt_hash="2" * 64,
        tool_hash="3" * 64,
        context_hash="4" * 64,
        config_hash="0" * 64,
        attempt_id=UUID(int=1),
    )


def _report() -> InvestigationReport:
    return InvestigationReport(
        conversation_id="seller-risk-investigation-v1",
        report_id=UUID(int=20),
        claims=(
            InvestigationClaim(
                claim_id="seller_risk",
                text="The evidence supports investigation.",
                evidence_refs=("query:baseline",),
            ),
        ),
        available_evidence=(
            InvestigationEvidence(
                evidence_id="query:baseline",
                kind="query",
                summary="Baseline evidence.",
                digest="a" * 64,
            ),
            InvestigationEvidence(
                evidence_id="reconciliation:baseline",
                kind="reconciliation",
                summary="Checks passed.",
                digest="b" * 64,
            ),
        ),
        reconciliation_refs=("reconciliation:baseline",),
        proposal_refs=({"proposal_id": UUID(int=30), "version": 1},),
    )


class ScriptedGraph:
    def __init__(self, outcomes: list[RetailRunOutcome]) -> None:
        self._outcomes = outcomes
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        return self._outcomes.pop(0)


class RecordingWorkflow:
    def __init__(self) -> None:
        self.decisions = []
        self.executions = []

    async def decide(self, request):
        self.decisions.append(request)
        grant = ExecutionGrant(
            proposal_id=request.proposal_ref.proposal_id,
            proposal_version=request.proposal_ref.version,
            payload_sha256="c" * 64,
            requester_id=UUID(int=1),
            approver_id=request.actor.actor_id,
            target_versions={"investigation_task": 0},
            expires_at=NOW,
            nonce="nonce-1",
            key_version=1,
            signature="d" * 64,
        )
        return DecisionReceipt(
            proposal_ref=request.proposal_ref,
            status=ProposalStatus.APPROVED,
            approver_id=request.actor.actor_id,
            decided_at=NOW,
            reason=request.reason,
            grant=grant,
        )

    async def execute(self, request):
        self.executions.append(request)
        return ExecutionReceipt(
            execution_id=UUID(int=40),
            proposal_ref={
                "proposal_id": request.grant.proposal_id,
                "version": request.grant.proposal_version,
            },
            status=ExecutionStatus.SUCCEEDED,
            command_type="open_seller_risk_case",
            before_version=0,
            after_version=1,
            committed_at=NOW,
            public_summary="Seller risk case opened.",
            audit_ref="audit:40",
        )


class QueryPort:
    def __init__(self) -> None:
        self.requests = []

    async def execute(self, request):
        self.requests.append(request)
        return QueryResult(columns=["task_ref"], rows=[{"task_ref": "task:40"}])


class Actors:
    def resolve(self, actor_ref: str) -> ActorContext:
        assert actor_ref == "actor:approver"
        return ActorContext(
            actor_id=UUID(int=2),
            role=ActorRole.APPROVER,
            authentication_ref="session:verified:2",
            authenticated_at=NOW,
        )


@pytest.mark.asyncio
async def test_driver_uses_new_attempt_for_clarification_then_executes_and_reads_back() -> None:
    clarification = ClarificationRequest(
        conversation_id="seller-risk-investigation-v1",
        request_id=UUID(int=10),
        items=(
            ClarificationItem(
                slot="time_field",
                question="Which time field?",
                allowed_values=("order_purchase_timestamp",),
            ),
        ),
    )
    graph = ScriptedGraph([_outcome(clarification), _outcome(_report())])
    workflow = RecordingWorkflow()
    query = QueryPort()
    reset = InMemoryScenarioReset()
    scenario = ProductScenario.model_validate(scenario_payload())
    driver = ProductScenarioDriver(
        retail_graph=graph,
        operation_workflow=workflow,
        query_engine=query,
        actors=Actors(),
        reset=reset,
    )

    result = await driver.run(scenario)

    assert result.status == "passed"
    assert result.asked_slots == ("time_field",)
    assert result.execution_count == 1
    assert result.audit_event_count == 1
    assert result.readback_count == 1
    assert graph.requests[0].run_scope.subject_id == graph.requests[1].run_scope.subject_id
    assert graph.requests[0].attempt_id != graph.requests[1].attempt_id
    assert reset.calls == [
        (scenario.scenario_id, scenario.reset_manifest_revision),
        (scenario.scenario_id, scenario.reset_manifest_revision),
    ]


@pytest.mark.asyncio
async def test_unregistered_clarification_uses_fixed_response_and_counts_invalid() -> None:
    payload = scenario_payload()
    payload["scripted_clarifications"] = []
    payload["required_clarification_slots"] = []
    clarification = ClarificationRequest(
        conversation_id="seller-risk-investigation-v1",
        request_id=UUID(int=11),
        items=(ClarificationItem(slot="time_field", question="Which time field?"),),
    )
    graph = ScriptedGraph([_outcome(clarification), _outcome(_report())])
    driver = ProductScenarioDriver(
        retail_graph=graph,
        operation_workflow=RecordingWorkflow(),
        query_engine=QueryPort(),
        actors=Actors(),
        reset=InMemoryScenarioReset(),
    )

    result = await driver.run(ProductScenario.model_validate(payload))

    assert result.invalid_clarification_count == 1
    assert graph.requests[1].confirmed_facts[0].content == "无法提供更多信息"


class RaisingGraph:
    async def run(self, request):
        del request
        raise RuntimeError("scenario failed")


@pytest.mark.asyncio
async def test_driver_still_runs_post_reset_when_product_execution_raises() -> None:
    reset = InMemoryScenarioReset()
    driver = ProductScenarioDriver(
        retail_graph=RaisingGraph(),
        operation_workflow=RecordingWorkflow(),
        query_engine=QueryPort(),
        actors=Actors(),
        reset=reset,
    )
    scenario = ProductScenario.model_validate(scenario_payload())

    with pytest.raises(RuntimeError, match="scenario failed"):
        await driver.run(scenario)

    assert reset.calls == [
        (scenario.scenario_id, scenario.reset_manifest_revision),
        (scenario.scenario_id, scenario.reset_manifest_revision),
    ]


class FailingReset(InMemoryScenarioReset):
    def __init__(self, fail_on_call: int) -> None:
        super().__init__()
        self._fail_on_call = fail_on_call

    async def reset(self, scenario_id: str, manifest_revision: str) -> None:
        await super().reset(scenario_id, manifest_revision)
        if len(self.calls) == self._fail_on_call:
            raise RuntimeError("reset failed")


@pytest.mark.asyncio
async def test_pre_reset_failure_prevents_product_execution() -> None:
    graph = ScriptedGraph([_outcome(_report())])
    driver = ProductScenarioDriver(
        retail_graph=graph,
        operation_workflow=RecordingWorkflow(),
        query_engine=QueryPort(),
        actors=Actors(),
        reset=FailingReset(1),
    )

    result = await driver.run(ProductScenario.model_validate(scenario_payload()))

    assert result.status == "failed"
    assert result.checks[0].reason_code == "pre_reset_failed"
    assert graph.requests == []


@pytest.mark.asyncio
async def test_post_reset_failure_appends_cleanup_failure() -> None:
    graph = ScriptedGraph([_outcome(_report())])
    driver = ProductScenarioDriver(
        retail_graph=graph,
        operation_workflow=RecordingWorkflow(),
        query_engine=QueryPort(),
        actors=Actors(),
        reset=FailingReset(2),
    )

    result = await driver.run(ProductScenario.model_validate(scenario_payload()))

    assert result.status == "failed"
    assert result.checks[-1].reason_code == "post_reset_failed"
    assert result.execution_count == 1


@pytest.mark.asyncio
async def test_driver_checks_operation_command_type_not_only_total_count() -> None:
    payload = scenario_payload()
    payload["operation_expectations"] = [
        {
            "command_type": "create_investigation_task",
            "terminal_status": "succeeded",
            "count": 1,
        }
    ]
    driver = ProductScenarioDriver(
        retail_graph=ScriptedGraph([_outcome(_report())]),
        operation_workflow=RecordingWorkflow(),
        query_engine=QueryPort(),
        actors=Actors(),
        reset=InMemoryScenarioReset(),
    )

    result = await driver.run(ProductScenario.model_validate(payload))

    assert result.status == "failed"
    assert "operation_count_mismatch" in {
        check.reason_code for check in result.checks
    }
