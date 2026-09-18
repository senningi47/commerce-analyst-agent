import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from commerce_agent.operations._approval import ApprovalService, HmacApprovalKeyring
from commerce_agent.operations._memory import InMemoryOperationStore, InvestigationTaskState
from commerce_agent.operations._store import DefaultReferenceValidator
from commerce_agent.operations.commands import AssignInvestigation, CreateInvestigationTask
from commerce_agent.operations.contracts import (
    ActorContext,
    ActorRole,
    DecisionRequest,
    EvidenceRef,
    ExecuteRequest,
    ExecutionGrant,
    InvestigationStatus,
    InvestigationTaskRef,
    ProposeRequest,
)
from commerce_agent.operations.errors import OperationError
from commerce_agent.operations.workflow import OperationWorkflow
from commerce_agent.orchestration.contracts import (
    InvestigationClaim,
    InvestigationEvidence,
    InvestigationReport,
    RetailRunOutcome,
)
from commerce_agent.product_eval import InMemoryScenarioReset, ProductScenario
from commerce_agent.product_eval.driver import ProductScenarioDriver
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


def load_negative_scenario(scenario_id: str) -> ProductScenario:
    document = json.loads(REGRESSION_FIXTURE.read_text(encoding="utf-8"))
    payload = next(
        item for item in document["scenarios"] if item["scenario_id"] == scenario_id
    )
    return ProductScenario.model_validate(payload)


class MutableClock:
    def __init__(self) -> None:
        self.value = NOW

    def now(self):
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


def _evidence() -> EvidenceRef:
    return EvidenceRef(
        kind="operation", ref="operation:authorization", digest="a" * 64
    )


def _create_command() -> CreateInvestigationTask:
    return CreateInvestigationTask(
        type="create_investigation_task",
        title="Authorization boundary probe",
        priority="high",
        public_summary="No write is allowed without a valid independent approval.",
        evidence_refs=(_evidence(),),
    )


class NegativeAuthorizationGraph:
    def __init__(
        self,
        scenario_id: str,
        workflow: OperationWorkflow,
        store: InMemoryOperationStore,
        clock: MutableClock,
        actors: Actors,
    ) -> None:
        self._scenario_id = scenario_id
        self._workflow = workflow
        self._store = store
        self._clock = clock
        self._actors = actors
        self.reason_code: str | None = None
        self.attempt_write_delta = -1

    async def run(self, request) -> RetailRunOutcome:
        before = self._store.business_write_count
        try:
            await self._attempt()
        except OperationError as error:
            self.reason_code = error.reason_code
        if self.attempt_write_delta < 0:
            self.attempt_write_delta = self._store.business_write_count - before
        evidence = InvestigationEvidence(
            evidence_id="operation:authorization",
            kind="operation",
            summary="Stable public authorization outcome.",
            digest="b" * 64,
        )
        report = InvestigationReport(
            conversation_id=request.run_scope.subject_id,
            report_id=request.attempt_id,
            claims=(
                InvestigationClaim(
                    claim_id="authorization_result",
                    text="The unauthorized operation added no business write.",
                    evidence_refs=(evidence.evidence_id,),
                ),
            ),
            available_evidence=(evidence,),
            reconciliation_refs=(evidence.evidence_id,),
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

    async def _proposal(self, *, key: str = "negative-proposal"):
        return await self._workflow.propose(
            ProposeRequest(
                actor=self._actors.analyst,
                command=_create_command(),
                evidence_refs=(_evidence(),),
                idempotency_key=key,
            )
        )

    async def _approve(self, proposal_ref):
        return await self._workflow.decide(
            DecisionRequest(
                actor=self._actors.approver,
                proposal_ref=proposal_ref,
                decision="approve",
                reason="independent review",
            )
        )

    async def _attempt(self) -> None:
        proposal = await self._proposal()
        if self._scenario_id == "unapproved-execution-v1":
            await self._workflow.execute(
                ExecuteRequest(
                    actor=self._actors.approver,
                    grant=ExecutionGrant(
                        proposal_id=proposal.proposal_ref.proposal_id,
                        proposal_version=proposal.proposal_ref.version,
                        payload_sha256=proposal.payload_sha256,
                        requester_id=self._actors.analyst.actor_id,
                        approver_id=self._actors.approver.actor_id,
                        target_versions=proposal.preview.target_versions,
                        expires_at=self._clock.now() + timedelta(minutes=10),
                        nonce="not-approved",
                        key_version=1,
                        signature="0" * 64,
                    ),
                )
            )
            return
        if self._scenario_id == "self-approval-v1":
            same_person = ActorContext(
                actor_id=self._actors.analyst.actor_id,
                role=ActorRole.APPROVER,
                authentication_ref="session:verified:same-person",
                authenticated_at=self._clock.now(),
            )
            await self._workflow.decide(
                DecisionRequest(
                    actor=same_person,
                    proposal_ref=proposal.proposal_ref,
                    decision="approve",
                    reason="not independent",
                )
            )
            return
        if self._scenario_id == "expired-proposal-v1":
            self._clock.advance(timedelta(hours=25))
            await self._approve(proposal.proposal_ref)
            return

        decision = await self._approve(proposal.proposal_ref)
        assert decision.grant is not None
        if self._scenario_id == "tampered-grant-v1":
            await self._workflow.execute(
                ExecuteRequest(
                    actor=self._actors.approver,
                    grant=decision.grant.model_copy(update={"signature": "0" * 64}),
                )
            )
            return
        if self._scenario_id == "expired-grant-v1":
            self._clock.advance(timedelta(minutes=11))
            await self._workflow.execute(
                ExecuteRequest(actor=self._actors.approver, grant=decision.grant)
            )
            return
        if self._scenario_id == "replayed-grant-v1":
            execute = ExecuteRequest(actor=self._actors.approver, grant=decision.grant)
            first = await self._workflow.execute(execute)
            before_replay = self._store.business_write_count
            repeated = await self._workflow.execute(execute)
            assert repeated == first
            assert self._store.business_write_count == before_replay
            self.reason_code = "idempotent_replay"
            self.attempt_write_delta = self._store.business_write_count - before_replay
            return
        raise AssertionError("target-version conflict uses a dedicated setup")


async def negative_driver(
    scenario_id: str,
) -> tuple[
    ProductScenarioDriver,
    ProductScenario,
    NegativeAuthorizationGraph,
    InMemoryOperationStore,
]:
    scenario = load_negative_scenario(scenario_id)
    actors = Actors()
    clock = MutableClock()
    initial_tasks = ()
    if scenario_id == "target-version-conflict-v1":
        initial_tasks = (
            InvestigationTaskState(
                task_id=UUID(int=700),
                status=InvestigationStatus.OPEN,
                priority="high",
                public_summary="Existing investigation.",
                version=2,
            ),
        )
    store = InMemoryOperationStore(initial_tasks=initial_tasks)
    workflow = OperationWorkflow(
        store=store,
        references=DefaultReferenceValidator(),
        clock=clock,
        approvals=ApprovalService(
            clock=clock,
            nonce_source=SequenceNonceSource(),
            keyring=HmacApprovalKeyring({1: b"authorization-test-key"}, active_version=1),
        ),
    )
    graph = NegativeAuthorizationGraph(scenario_id, workflow, store, clock, actors)
    if scenario_id == "target-version-conflict-v1":
        stale_evidence = _evidence()
        stale_command = AssignInvestigation(
            type="assign_investigation",
            task_ref=InvestigationTaskRef(task_id=UUID(int=700), version=1),
            expected_target_version=1,
            assignee_ref="actor:assignee",
            evidence_refs=(stale_evidence,),
        )

        async def conflict_attempt() -> None:
            await workflow.propose(
                ProposeRequest(
                    actor=actors.analyst,
                    command=stale_command,
                    evidence_refs=(stale_evidence,),
                    idempotency_key="stale-target-version",
                )
            )

        graph._attempt = conflict_attempt
    driver = ProductScenarioDriver(
        retail_graph=graph,
        operation_workflow=workflow,
        query_engine=StoreReadbackPort(store),
        actors=actors,
        reset=InMemoryScenarioReset(),
    )
    return driver, scenario, graph, store


@pytest.mark.parametrize(
    ("scenario_id", "expected_reason"),
    [
        ("unapproved-execution-v1", "approval_required"),
        ("self-approval-v1", "self_approval_denied"),
        ("tampered-grant-v1", "signature_invalid"),
        ("expired-proposal-v1", "proposal_expired"),
        ("expired-grant-v1", "grant_expired"),
        ("replayed-grant-v1", "idempotent_replay"),
        ("target-version-conflict-v1", "target_version_conflict"),
    ],
)
@pytest.mark.asyncio
async def test_negative_operation_scenarios_write_nothing(
    scenario_id: str, expected_reason: str
) -> None:
    driver, scenario, graph, store = await negative_driver(scenario_id)

    result = await driver.run(scenario)

    assert result.status == "passed"
    assert result.successful_business_write_count == 0
    assert result.successful_execution_audit_count == 0
    assert graph.reason_code == expected_reason
    assert graph.attempt_write_delta == 0
    if scenario_id != "replayed-grant-v1":
        assert store.business_write_count == 0
        assert store.audit_count == 0
