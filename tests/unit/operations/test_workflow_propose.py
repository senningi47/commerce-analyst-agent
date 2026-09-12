from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from commerce_agent.operations._approval import (
    ApprovalService,
    FixedClock,
    FixedNonceSource,
    HmacApprovalKeyring,
)
from commerce_agent.operations._memory import InMemoryOperationStore
from commerce_agent.operations._seller_refs import (
    InMemorySellerIdentityStore,
    PrivateSellerObservation,
    SellerRefKeyring,
    SellerTargetResolver,
)
from commerce_agent.operations._store import DefaultReferenceValidator
from commerce_agent.operations.commands import CreateInvestigationTask, OpenSellerRiskCase
from commerce_agent.operations.contracts import (
    ActorContext,
    ActorRole,
    EvidenceRef,
    MetricRef,
    ProposalRef,
    ProposeRequest,
    SellerTargetRequest,
)
from commerce_agent.operations.errors import (
    OperationAuthorizationError,
    OperationConflictError,
    OperationContractError,
)
from commerce_agent.operations.workflow import OperationWorkflow

NOW = datetime(2026, 9, 6, 4, 0, tzinfo=UTC)
EVIDENCE = EvidenceRef(kind="query", ref="query:evidence-1", digest="a" * 64)


def actor(role: str = "analyst", actor_id: int = 1) -> ActorContext:
    return ActorContext(
        actor_id=UUID(int=actor_id),
        role=ActorRole(role),
        authentication_ref=f"session:verified:{actor_id}",
        authenticated_at=NOW,
    )


def workflow_fixture() -> tuple[OperationWorkflow, InMemoryOperationStore]:
    store = InMemoryOperationStore()
    workflow = OperationWorkflow(
        store=store,
        references=DefaultReferenceValidator(),
        clock=FixedClock(NOW),
        approvals=ApprovalService(
            clock=FixedClock(NOW),
            nonce_source=FixedNonceSource(bytes(range(32))),
            keyring=HmacApprovalKeyring({1: b"day4-test-key"}, active_version=1),
        ),
    )
    return workflow, store


def valid_propose_request(
    *,
    actor_context: ActorContext | None = None,
    key: str = "proposal-key-1",
    title: str = "Review delayed deliveries",
    revises: ProposalRef | None = None,
) -> ProposeRequest:
    return ProposeRequest(
        actor=actor_context or actor(),
        command=CreateInvestigationTask(
            type="create_investigation_task",
            title=title,
            priority="high",
            public_summary="Validate the affected cohort and document findings.",
            evidence_refs=(EVIDENCE,),
            expected_target_version=0,
        ),
        evidence_refs=(EVIDENCE,),
        idempotency_key=key,
        revises=revises,
    )


@pytest.mark.asyncio
async def test_only_analyst_can_create_one_immutable_proposal() -> None:
    workflow, store = workflow_fixture()
    request = valid_propose_request(actor_context=actor("analyst", 1))

    first = await workflow.propose(request)
    repeated = await workflow.propose(request)

    assert first == repeated
    assert first.status == "pending"
    assert first.proposal_ref.version == 1
    assert store.business_write_count == 0
    with pytest.raises(OperationAuthorizationError) as caught:
        await workflow.propose(
            request.model_copy(update={"actor": actor("approver", 2)})
        )
    assert caught.value.reason_code == "analyst_required"


@pytest.mark.asyncio
async def test_revising_pending_proposal_supersedes_old_version() -> None:
    workflow, store = workflow_fixture()
    first = await workflow.propose(valid_propose_request(key="proposal-v1"))
    second = await workflow.propose(
        valid_propose_request(
            key="proposal-v2", revises=first.proposal_ref, title="Revised scope"
        )
    )

    assert second.proposal_ref.proposal_id == first.proposal_ref.proposal_id
    assert second.proposal_ref.version == 2
    assert await store.status(first.proposal_ref) == "superseded"


@pytest.mark.asyncio
async def test_same_idempotency_key_with_changed_payload_fails() -> None:
    workflow, _store = workflow_fixture()
    await workflow.propose(valid_propose_request(key="same-key"))
    with pytest.raises(OperationConflictError) as caught:
        await workflow.propose(valid_propose_request(key="same-key", title="Changed"))
    assert caught.value.reason_code == "idempotency_conflict"


@pytest.mark.asyncio
async def test_open_seller_risk_case_ref_must_match_query_evidence() -> None:
    resolver = SellerTargetResolver(
        store=InMemorySellerIdentityStore(
            (
                PrivateSellerObservation(
                    seller_id="seller-private-001",
                    numerator=3,
                    denominator=10,
                    normalized_value=Decimal("0.3000"),
                ),
            )
        ),
        keyring=SellerRefKeyring({1: b"seller-test-key"}, active_version=1),
        clock=FixedClock(NOW),
    )
    issued = (
        await resolver.find_candidates(
            SellerTargetRequest(
                metric_ref=MetricRef(name="late_delivery_rate", revision="v1"),
                observation_started_at=NOW.replace(month=8),
                observation_ended_at=NOW,
                anomaly_threshold=Decimal("0.2000"),
                minimum_denominator=5,
                status_filters=("delivered",),
                evidence_digest="a" * 64,
            )
        )
    )[0].seller_ref
    wrong_evidence = EvidenceRef(kind="query", ref="query:wrong", digest="b" * 64)
    store = InMemoryOperationStore()
    workflow = OperationWorkflow(
        store=store,
        references=DefaultReferenceValidator(seller_resolver=resolver),
        clock=FixedClock(NOW),
        approvals=ApprovalService(
            clock=FixedClock(NOW),
            nonce_source=FixedNonceSource(bytes(range(32))),
            keyring=HmacApprovalKeyring({1: b"day4-test-key"}, active_version=1),
        ),
    )
    command = OpenSellerRiskCase(
        type="open_seller_risk_case",
        seller_ref=issued,
        observation_started_at=NOW.replace(month=8),
        observation_ended_at=NOW,
        metric_ref=MetricRef(name="late_delivery_rate", revision="v1"),
        numerator=3,
        denominator=10,
        observed=Decimal("0.3000"),
        threshold=Decimal("0.2000"),
        title="Investigate seller delivery risk",
        priority="high",
        evidence_refs=(wrong_evidence,),
        expected_target_version=0,
    )

    with pytest.raises(OperationContractError) as caught:
        await workflow.propose(
            ProposeRequest(
                actor=actor(),
                command=command,
                evidence_refs=(wrong_evidence,),
                idempotency_key="seller-risk-mismatch",
            )
        )

    assert caught.value.reason_code == "seller_ref_evidence_mismatch"
    assert store.proposal_count == 0
