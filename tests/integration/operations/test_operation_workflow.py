import os
from uuid import uuid4

import pytest
from pydantic import SecretStr

from commerce_agent.operations._approval import (
    ApprovalService,
    HmacApprovalKeyring,
    SecretsNonceSource,
    SystemClock,
)
from commerce_agent.operations._postgres import PostgresOperationStore
from commerce_agent.operations._store import DefaultReferenceValidator
from commerce_agent.operations.commands import CreateInvestigationTask
from commerce_agent.operations.contracts import (
    ActorContext,
    DecisionRequest,
    EvidenceRef,
    ExecuteRequest,
    ExecutionStatus,
    ProposalStatus,
    ProposeRequest,
)
from commerce_agent.operations.workflow import OperationWorkflow

pytestmark = pytest.mark.postgres


def operation_store(scenario_id: str) -> PostgresOperationStore:
    return PostgresOperationStore(
        proposal_dsn=SecretStr(os.environ["PRODUCT_PROPOSAL_DATABASE_DSN"]),
        approval_dsn=SecretStr(os.environ["PRODUCT_APPROVAL_DATABASE_DSN"]),
        execution_dsn=SecretStr(os.environ["PRODUCT_OPERATION_DATABASE_DSN"]),
        scenario_id=scenario_id,
    )


def operation_workflow(store: PostgresOperationStore) -> OperationWorkflow:
    return OperationWorkflow(
        store=store,
        references=DefaultReferenceValidator(),
        clock=SystemClock(),
        approvals=ApprovalService(
            clock=SystemClock(),
            nonce_source=SecretsNonceSource(),
            keyring=HmacApprovalKeyring(
                {1: os.environ["PRODUCT_OPERATION_APPROVAL_HMAC_KEY_V1"].encode("utf-8")},
                active_version=1,
            ),
        ),
    )


def create_task_request(actor: ActorContext, *, suffix: str) -> ProposeRequest:
    evidence = EvidenceRef(
        kind="query",
        ref=f"query:task15:{suffix}",
        digest="a" * 64,
    )
    command = CreateInvestigationTask(
        type="create_investigation_task",
        title="Investigate a reviewed fulfillment exception",
        priority="high",
        public_summary="Investigate one evidence-bound fulfillment exception.",
        evidence_refs=(evidence,),
        expected_target_version=0,
    )
    return ProposeRequest(
        actor=actor,
        command=command,
        evidence_refs=(evidence,),
        idempotency_key=f"task15-workflow-{suffix}",
    )


@pytest.mark.asyncio
async def test_role_bound_workflow_commits_and_reads_one_receipt(
    clean_product_scenario: None,
    postgres_workflow: OperationWorkflow,
    product_scenario_id: str,
    analyst_actor: ActorContext,
    approver_actor: ActorContext,
) -> None:
    del clean_product_scenario
    request = create_task_request(analyst_actor, suffix=uuid4().hex)

    proposal = await postgres_workflow.propose(request)
    decision = await operation_workflow(operation_store(product_scenario_id)).decide(
        DecisionRequest(
            actor=approver_actor,
            proposal_ref=proposal.proposal_ref,
            decision="approve",
        )
    )
    assert proposal.status is ProposalStatus.PENDING
    assert decision.status is ProposalStatus.APPROVED
    assert decision.grant is not None
    foreign_store = operation_store("task15-foreign-scenario-v1")
    assert await foreign_store.read_proposal(proposal.proposal_ref) is None
    assert await foreign_store.read_decision(proposal.proposal_ref) is None

    receipt = await operation_workflow(operation_store(product_scenario_id)).execute(
        ExecuteRequest(actor=approver_actor, grant=decision.grant)
    )
    recovered = await operation_store(product_scenario_id).read_execution(
        proposal.proposal_ref
    )

    assert receipt.status is ExecutionStatus.SUCCEEDED
    assert recovered == receipt
    assert receipt.before_version == 0
    assert receipt.after_version == 1
    assert receipt.audit_ref.startswith("audit:")
