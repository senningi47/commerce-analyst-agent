import asyncio
import base64
import hashlib
import os
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
import pytest
from pydantic import SecretStr

from commerce_agent.operations._approval import (
    ApprovalService,
    HmacApprovalKeyring,
    SecretsNonceSource,
    SystemClock,
)
from commerce_agent.operations._canonical import canonical_json_bytes
from commerce_agent.operations._postgres import PostgresOperationStore
from commerce_agent.operations._store import DefaultReferenceValidator
from commerce_agent.operations.commands import AssignInvestigation
from commerce_agent.operations.contracts import (
    ActorContext,
    DecisionRequest,
    EvidenceRef,
    ExecuteRequest,
    InvestigationTaskRef,
    ProposeRequest,
)
from commerce_agent.operations.errors import (
    OperationError,
    OperationInfrastructureError,
    OperationOutcomeUnknown,
)
from commerce_agent.operations.workflow import OperationWorkflow
from tests.integration.operations.test_operation_security import _scenario_counts
from tests.integration.operations.test_operation_workflow import create_task_request

pytestmark = pytest.mark.postgres

ConnectionFactory = Callable[..., Awaitable[Any]]


def _workflow_with_connect(
    scenario_id: str,
    connect: ConnectionFactory,
) -> tuple[OperationWorkflow, PostgresOperationStore]:
    store = PostgresOperationStore(
        proposal_dsn=SecretStr(os.environ["PRODUCT_PROPOSAL_DATABASE_DSN"]),
        approval_dsn=SecretStr(os.environ["PRODUCT_APPROVAL_DATABASE_DSN"]),
        execution_dsn=SecretStr(os.environ["PRODUCT_OPERATION_DATABASE_DSN"]),
        scenario_id=scenario_id,
        connect=connect,
    )
    workflow = OperationWorkflow(
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
    return workflow, store


async def _approved_request(
    workflow: OperationWorkflow,
    analyst: ActorContext,
    approver: ActorContext,
) -> tuple[object, ExecuteRequest]:
    proposal = await workflow.propose(
        create_task_request(analyst, suffix=uuid4().hex)
    )
    decision = await workflow.decide(
        DecisionRequest(
            actor=approver,
            proposal_ref=proposal.proposal_ref,
            decision="approve",
        )
    )
    assert decision.grant is not None
    return proposal, ExecuteRequest(actor=approver, grant=decision.grant)


@pytest.mark.asyncio
async def test_two_concurrent_execute_calls_commit_once(
    clean_product_scenario: None,
    postgres_workflow: OperationWorkflow,
    analyst_actor: ActorContext,
    approver_actor: ActorContext,
    product_scenario_id: str,
) -> None:
    del clean_product_scenario
    _proposal, request = await _approved_request(
        postgres_workflow, analyst_actor, approver_actor
    )

    results = await asyncio.gather(
        postgres_workflow.execute(request),
        postgres_workflow.execute(request),
    )

    assert results[0] == results[1]
    assert await _scenario_counts(product_scenario_id) == (1, 1, 1, 1)


@pytest.mark.asyncio
async def test_two_concurrent_decisions_record_only_one_terminal_decision(
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
    second_approver = approver_actor.model_copy(
        update={"actor_id": UUID("00000000-0000-0000-0000-000000000104")}
    )
    results = await asyncio.gather(
        postgres_workflow.decide(
            DecisionRequest(
                actor=approver_actor,
                proposal_ref=proposal.proposal_ref,
                decision="approve",
            )
        ),
        postgres_workflow.decide(
            DecisionRequest(
                actor=second_approver,
                proposal_ref=proposal.proposal_ref,
                decision="reject",
                reason="Independent reviewer rejected the action.",
            )
        ),
        return_exceptions=True,
    )

    assert sum(not isinstance(item, BaseException) for item in results) == 1
    assert sum(isinstance(item, OperationError) for item in results) == 1
    assert (await _scenario_counts(product_scenario_id))[:3] == (0, 0, 0)


@pytest.mark.asyncio
async def test_target_version_conflict_rolls_back_second_execution(
    clean_product_scenario: None,
    postgres_workflow: OperationWorkflow,
    analyst_actor: ActorContext,
    approver_actor: ActorContext,
    product_scenario_id: str,
) -> None:
    del clean_product_scenario
    proposal, request = await _approved_request(
        postgres_workflow, analyst_actor, approver_actor
    )
    await postgres_workflow.execute(request)
    task_ref = uuid5(
        NAMESPACE_URL,
        f"task:{proposal.proposal_ref.proposal_id}:{proposal.proposal_ref.version}",
    )
    evidence = EvidenceRef(kind="query", ref="query:stale-target", digest="b" * 64)
    stale = AssignInvestigation(
        type="assign_investigation",
        task_ref=InvestigationTaskRef(task_id=task_ref, version=99),
        expected_target_version=99,
        assignee_ref="team:risk-operations",
        evidence_refs=(evidence,),
    )
    stale_proposal = await postgres_workflow.propose(
        ProposeRequest(
            actor=analyst_actor,
            command=stale,
            evidence_refs=(evidence,),
            idempotency_key=f"stale-target-{uuid4().hex}",
        )
    )
    stale_decision = await postgres_workflow.decide(
        DecisionRequest(
            actor=approver_actor,
            proposal_ref=stale_proposal.proposal_ref,
            decision="approve",
        )
    )
    assert stale_decision.grant is not None

    with pytest.raises(OperationInfrastructureError) as caught:
        await postgres_workflow.execute(
            ExecuteRequest(actor=approver_actor, grant=stale_decision.grant)
        )

    assert caught.value.reason_code == "operation_serialization_failure"
    assert await _scenario_counts(product_scenario_id) == (1, 1, 1, 1)


@pytest.mark.asyncio
async def test_constraint_failure_rolls_back_nonce_business_execution_and_audit(
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
    grant = decision.grant
    raw_nonce = base64.urlsafe_b64decode(grant.nonce + "=" * (-len(grant.nonce) % 4))
    nonce_digest = hashlib.sha256(raw_nonce).hexdigest()
    target_digest = hashlib.sha256(
        canonical_json_bytes(grant.target_versions)
    ).hexdigest()
    execution_id = uuid5(
        NAMESPACE_URL,
        f"operation:{proposal.proposal_ref.proposal_id}:{proposal.proposal_ref.version}",
    )
    task_ref = uuid5(
        NAMESPACE_URL,
        f"task:{proposal.proposal_ref.proposal_id}:{proposal.proposal_ref.version}",
    )
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_OPERATION_DATABASE_DSN"]
    )
    try:
        await connection.execute("BEGIN")
        for statement in (
            "SET LOCAL statement_timeout = '5s'",
            "SET LOCAL lock_timeout = '1s'",
            "SET LOCAL idle_in_transaction_session_timeout = '10s'",
        ):
            await connection.execute(statement)
        with pytest.raises(psycopg.errors.NotNullViolation):
            await connection.execute(
                "SELECT * FROM trusted_schema.execute_create_investigation_task("
                + ", ".join(["%s"] * 14)
                + ")",
                (
                    execution_id,
                    proposal.proposal_ref.proposal_id,
                    proposal.proposal_ref.version,
                    approver_actor.actor_id,
                    grant.payload_sha256,
                    target_digest,
                    nonce_digest,
                    f"execute:{proposal.proposal_ref.proposal_id}:1",
                    product_scenario_id,
                    task_ref,
                    "Constraint rollback contract",
                    "high",
                    "Public summary remains valid.",
                    None,
                ),
            )
    finally:
        await connection.rollback()
        await connection.close()

    assert await _scenario_counts(product_scenario_id) == (0, 0, 0, 0)


class _CommitLossConnection:
    def __init__(
        self,
        connection: psycopg.AsyncConnection[object],
        owner: "_LoseOneExecuteCommit",
    ) -> None:
        self._connection = connection
        self._owner = owner
        self._typed_execution = False

    async def execute(self, query: str, params: object | None = None) -> Any:
        if "trusted_schema.execute_" in query:
            self._typed_execution = True
        return await self._connection.execute(query, params)

    async def commit(self) -> None:
        await self._connection.commit()
        if self._typed_execution and not self._owner.lost:
            self._owner.lost = True
            raise psycopg.OperationalError("commit response unavailable")

    async def rollback(self) -> None:
        await self._connection.rollback()

    async def close(self) -> None:
        await self._connection.close()


class _LoseOneExecuteCommit:
    def __init__(self) -> None:
        self.lost = False

    async def __call__(self, dsn: str, **kwargs: object) -> Any:
        connection = await psycopg.AsyncConnection.connect(dsn, **kwargs)
        if "operation_executor" in dsn and not self.lost:
            return _CommitLossConnection(connection, self)
        return connection


@pytest.mark.asyncio
async def test_post_commit_response_loss_recovers_exact_receipt(
    clean_product_scenario: None,
    analyst_actor: ActorContext,
    approver_actor: ActorContext,
    product_scenario_id: str,
) -> None:
    del clean_product_scenario
    workflow, store = _workflow_with_connect(product_scenario_id, _LoseOneExecuteCommit())
    proposal, request = await _approved_request(workflow, analyst_actor, approver_actor)

    with pytest.raises(OperationOutcomeUnknown):
        await workflow.execute(request)

    recovered = await store.read_execution(proposal.proposal_ref)
    assert recovered is not None
    assert await workflow.execute(request) == recovered
    assert await _scenario_counts(product_scenario_id) == (1, 1, 1, 1)
