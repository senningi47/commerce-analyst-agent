import os
from datetime import timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from commerce_agent.operations._approval import (
    ApprovalService,
    FixedClock,
    FixedNonceSource,
    HmacApprovalKeyring,
)
from commerce_agent.operations._postgres import PostgresOperationStore
from commerce_agent.operations._store import DefaultReferenceValidator
from commerce_agent.operations.contracts import (
    ActorContext,
    ActorRole,
    DecisionRequest,
    ExecuteRequest,
)
from commerce_agent.operations.errors import ApprovalError, OperationAuthorizationError
from commerce_agent.operations.workflow import OperationWorkflow
from tests.integration.operations.test_operation_workflow import create_task_request

pytestmark = pytest.mark.postgres


async def _scenario_counts(scenario_id: str) -> tuple[int, int, int, int]:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT "
            "(SELECT count(*) FROM ops.investigation_task WHERE scenario_id = %s), "
            "(SELECT count(*) FROM ops.command_execution "
            " WHERE scenario_id = %s AND status = 'succeeded'), "
            "(SELECT count(*) FROM ops.audit_event WHERE scenario_id = %s), "
            "(SELECT count(*) FROM ops.approval_nonce "
            " WHERE scenario_id = %s AND used_at IS NOT NULL)",
            (scenario_id, scenario_id, scenario_id, scenario_id),
        )
        row = await cursor.fetchone()
        assert row is not None
        return tuple(int(value) for value in row)
    finally:
        await connection.rollback()
        await connection.close()


@pytest.mark.asyncio
async def test_self_approval_produces_zero_business_writes(
    clean_product_scenario: None,
    postgres_workflow: OperationWorkflow,
    analyst_actor: ActorContext,
    product_scenario_id: str,
) -> None:
    del clean_product_scenario
    proposal = await postgres_workflow.propose(
        create_task_request(analyst_actor, suffix=uuid4().hex)
    )
    same_person_approver = analyst_actor.model_copy(update={"role": ActorRole.APPROVER})

    with pytest.raises(OperationAuthorizationError) as caught:
        await postgres_workflow.decide(
            DecisionRequest(
                actor=same_person_approver,
                proposal_ref=proposal.proposal_ref,
                decision="approve",
            )
        )

    assert caught.value.reason_code == "self_approval_denied"
    assert await _scenario_counts(product_scenario_id) == (0, 0, 0, 0)


@pytest.mark.asyncio
async def test_tampered_grant_produces_zero_business_writes(
    clean_product_scenario: None,
    postgres_workflow: OperationWorkflow,
    analyst_actor: ActorContext,
    approver_actor: ActorContext,
    product_scenario_id: str,
) -> None:
    del clean_product_scenario
    proposal = await postgres_workflow.propose(
        create_task_request(analyst_actor, suffix=uuid4().hex)
    )
    decision = await postgres_workflow.decide(
        DecisionRequest(
            actor=approver_actor,
            proposal_ref=proposal.proposal_ref,
            decision="approve",
        )
    )
    assert decision.grant is not None
    tampered = decision.grant.model_copy(update={"signature": "0" * 64})

    with pytest.raises(ApprovalError) as caught:
        await postgres_workflow.execute(ExecuteRequest(actor=approver_actor, grant=tampered))

    assert caught.value.reason_code == "signature_invalid"
    assert await _scenario_counts(product_scenario_id) == (0, 0, 0, 0)


@pytest.mark.asyncio
async def test_grant_bound_to_another_actor_produces_zero_business_writes(
    clean_product_scenario: None,
    postgres_workflow: OperationWorkflow,
    analyst_actor: ActorContext,
    approver_actor: ActorContext,
    product_scenario_id: str,
) -> None:
    del clean_product_scenario
    proposal = await postgres_workflow.propose(
        create_task_request(analyst_actor, suffix=uuid4().hex)
    )
    decision = await postgres_workflow.decide(
        DecisionRequest(
            actor=approver_actor,
            proposal_ref=proposal.proposal_ref,
            decision="approve",
        )
    )
    assert decision.grant is not None
    another_approver = approver_actor.model_copy(
        update={"actor_id": UUID("00000000-0000-0000-0000-000000000103")}
    )

    with pytest.raises(OperationAuthorizationError) as caught:
        await postgres_workflow.execute(
            ExecuteRequest(actor=another_approver, grant=decision.grant)
        )

    assert caught.value.reason_code == "grant_actor_mismatch"
    assert await _scenario_counts(product_scenario_id) == (0, 0, 0, 0)


@pytest.mark.asyncio
async def test_expired_grant_produces_zero_business_writes(
    clean_product_scenario: None,
    postgres_workflow: OperationWorkflow,
    postgres_operation_store: PostgresOperationStore,
    analyst_actor: ActorContext,
    approver_actor: ActorContext,
    product_scenario_id: str,
) -> None:
    del clean_product_scenario
    proposal = await postgres_workflow.propose(
        create_task_request(analyst_actor, suffix=uuid4().hex)
    )
    decision = await postgres_workflow.decide(
        DecisionRequest(
            actor=approver_actor,
            proposal_ref=proposal.proposal_ref,
            decision="approve",
        )
    )
    assert decision.grant is not None
    expired_clock = FixedClock(decision.grant.expires_at + timedelta(seconds=1))
    expired_workflow = OperationWorkflow(
        store=postgres_operation_store,
        references=DefaultReferenceValidator(),
        clock=expired_clock,
        approvals=ApprovalService(
            clock=expired_clock,
            nonce_source=FixedNonceSource(bytes(range(32))),
            keyring=HmacApprovalKeyring(
                {1: os.environ["PRODUCT_OPERATION_APPROVAL_HMAC_KEY_V1"].encode("utf-8")},
                active_version=1,
            ),
        ),
    )

    with pytest.raises(ApprovalError) as caught:
        await expired_workflow.execute(
            ExecuteRequest(actor=approver_actor, grant=decision.grant)
        )

    assert caught.value.reason_code == "grant_expired"
    assert await _scenario_counts(product_scenario_id) == (0, 0, 0, 0)
