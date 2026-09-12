import json
from pathlib import Path

import pytest

from commerce_agent.operations._approval import ApprovalService, FixedClock, HmacApprovalKeyring
from commerce_agent.operations._memory import InMemoryOperationStore
from commerce_agent.operations._store import DefaultReferenceValidator
from commerce_agent.operations.commands import CreateInvestigationTask
from commerce_agent.operations.contracts import EvidenceRef, ProposeRequest
from commerce_agent.operations.workflow import OperationWorkflow
from commerce_agent.orchestration.contracts import (
    InvestigationClaim,
    InvestigationEvidence,
    InvestigationReport,
    RetailRunOutcome,
)
from commerce_agent.product_eval import InMemoryScenarioReset, ProductScenario
from commerce_agent.product_eval.driver import ProductScenarioDriver
from tests.support.product_eval_failures import (
    CommitOutcomeUnknown,
    DropProposalResponseAfterCommit,
    DropResponseAfterCommit,
    FailBeforeExecuteCall,
    FailBeforeProposalPersist,
    InjectedFailure,
)
from tests.unit.product_eval.test_day4_scenarios import (
    NOW,
    Actors,
    SequenceNonceSource,
    StoreReadbackPort,
)

REGRESSION_FIXTURE = (
    Path(__file__).parents[3]
    / "tests"
    / "fixtures"
    / "product_eval"
    / "day4-regression.v1.json"
)


def load_recovery_scenario(failure_point: str) -> ProductScenario:
    document = json.loads(REGRESSION_FIXTURE.read_text(encoding="utf-8"))
    payload = next(
        item
        for item in document["scenarios"]
        if item["allowed_failure_script"] == [failure_point]
    )
    return ProductScenario.model_validate(payload)


class RecoveryWorkflow:
    def __init__(self, inner: OperationWorkflow) -> None:
        self._inner = inner

    async def propose(self, request):
        return await self._inner.propose(request)

    async def decide(self, request):
        return await self._inner.decide(request)

    async def execute(self, request):
        try:
            return await self._inner.execute(request)
        except (InjectedFailure, CommitOutcomeUnknown):
            return await self._inner.execute(request)


class RecoveryGraph:
    def __init__(
        self,
        scenario: ProductScenario,
        workflow: RecoveryWorkflow,
        actors: Actors,
    ) -> None:
        self._scenario = scenario
        self._workflow = workflow
        self._actors = actors

    async def run(self, request) -> RetailRunOutcome:
        evidence = EvidenceRef(
            kind="operation", ref="operation:recovery", digest="e" * 64
        )
        command = CreateInvestigationTask(
            type="create_investigation_task",
            title="Recover deterministic operation",
            priority="high",
            public_summary="Retry only with the original operation identity.",
            evidence_refs=(evidence,),
        )
        proposal_request = ProposeRequest(
            actor=self._actors.analyst,
            command=command,
            evidence_refs=(evidence,),
            idempotency_key=f"{self._scenario.scenario_id}:0",
        )
        try:
            proposal = await self._workflow.propose(proposal_request)
        except (InjectedFailure, CommitOutcomeUnknown):
            proposal = await self._workflow.propose(proposal_request)
        report = InvestigationReport(
            conversation_id=request.run_scope.subject_id,
            report_id=request.attempt_id,
            claims=(
                InvestigationClaim(
                    claim_id="recovery_result",
                    text="The operation identity was recovered deterministically.",
                    evidence_refs=("operation:recovery",),
                ),
            ),
            available_evidence=(
                InvestigationEvidence(
                    evidence_id="operation:recovery",
                    kind="operation",
                    summary="Public recovery outcome.",
                    digest="f" * 64,
                ),
            ),
            reconciliation_refs=("operation:recovery",),
            proposal_refs=(proposal.proposal_ref,),
        )
        return RetailRunOutcome(
            status="completed",
            terminal=report,
            model_calls=1,
            tool_calls=1,
            prompt_policy_hash="1" * 64,
            rendered_prompt_hash="2" * 64,
            tool_hash="3" * 64,
            context_hash="4" * 64,
            config_hash=request.run_scope.config_hash,
            attempt_id=request.attempt_id,
        )


async def recovery_driver(
    failure_point: str,
) -> tuple[ProductScenarioDriver, ProductScenario, InMemoryOperationStore]:
    scenario = load_recovery_scenario(failure_point)
    inner_store = InMemoryOperationStore()
    key = f"{scenario.scenario_id}:0"
    decorated_store = {
        "before_proposal_persist": FailBeforeProposalPersist(inner_store, once_for=key),
        "after_proposal_commit_response_lost": DropProposalResponseAfterCommit(
            inner_store, once_for=key
        ),
        "before_execute_call": FailBeforeExecuteCall(inner_store, once_for=key),
        "after_execute_commit_response_lost": DropResponseAfterCommit(
            inner_store, once_for=key
        ),
    }[failure_point]
    workflow = RecoveryWorkflow(
        OperationWorkflow(
            store=decorated_store,
            references=DefaultReferenceValidator(),
            clock=FixedClock(NOW),
            approvals=ApprovalService(
                clock=FixedClock(NOW),
                nonce_source=SequenceNonceSource(),
                keyring=HmacApprovalKeyring(
                    {1: b"recovery-operation-key"}, active_version=1
                ),
            ),
        )
    )
    actors = Actors()
    driver = ProductScenarioDriver(
        retail_graph=RecoveryGraph(scenario, workflow, actors),
        operation_workflow=workflow,
        query_engine=StoreReadbackPort(inner_store),
        actors=actors,
        reset=InMemoryScenarioReset(),
    )
    return driver, scenario, inner_store


@pytest.mark.parametrize(
    ("failure_point", "expected_proposals", "expected_writes"),
    [
        ("before_proposal_persist", 1, 0),
        ("after_proposal_commit_response_lost", 1, 0),
        ("before_execute_call", 1, 1),
        ("after_execute_commit_response_lost", 1, 1),
    ],
)
@pytest.mark.asyncio
async def test_failure_recovery_is_idempotent(
    failure_point: str,
    expected_proposals: int,
    expected_writes: int,
) -> None:
    driver, scenario, store = await recovery_driver(failure_point)

    result = await driver.run(scenario)

    assert result.status == "passed"
    assert result.unique_proposal_count == expected_proposals
    assert result.unique_business_write_count == expected_writes
    assert result.duplicate_audit_count == 0
    assert store.proposal_count == expected_proposals
    assert store.business_write_count == expected_writes
    assert store.audit_count == expected_writes
