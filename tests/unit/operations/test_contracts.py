from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from commerce_agent.operations.commands import CreateInvestigationTask
from commerce_agent.operations.contracts import (
    ActorContext,
    ActorRole,
    DecisionReceipt,
    EvidenceRef,
    ExecutionGrant,
    ProposalRef,
    ProposalStatus,
    ProposeRequest,
)

NOW = datetime(2026, 9, 6, 4, 0, tzinfo=UTC)


def valid_actor() -> ActorContext:
    return ActorContext(
        actor_id=UUID(int=1),
        role=ActorRole.ANALYST,
        authentication_ref="session:verified:1",
        authenticated_at=NOW,
    )


def valid_evidence() -> EvidenceRef:
    return EvidenceRef(kind="query", ref="query:evidence-1", digest="a" * 64)


def valid_grant() -> ExecutionGrant:
    return ExecutionGrant(
        proposal_id=UUID(int=16),
        proposal_version=1,
        payload_sha256="a" * 64,
        requester_id=UUID(int=1),
        approver_id=UUID(int=2),
        target_versions={"investigation_task": 0},
        expires_at=NOW + timedelta(minutes=10),
        nonce="AQID",
        key_version=1,
        signature="b" * 64,
    )


def test_propose_request_has_one_trusted_actor_and_one_frozen_command() -> None:
    actor = valid_actor()
    evidence = valid_evidence()
    command = CreateInvestigationTask(
        type="create_investigation_task",
        title="Review delayed deliveries",
        priority="high",
        public_summary="Validate the affected cohort and document findings.",
        evidence_refs=(evidence,),
        expected_target_version=0,
    )
    request = ProposeRequest(
        actor=actor,
        command=command,
        evidence_refs=(evidence,),
        idempotency_key="day4-scenario-1-proposal-1",
    )

    assert request.command.type == "create_investigation_task"
    with pytest.raises(ValidationError):
        request.command.title = "changed"


def test_datetime_contracts_reject_naive_values() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        ActorContext(
            actor_id=UUID(int=1),
            role=ActorRole.ANALYST,
            authentication_ref="session:verified:1",
            authenticated_at=datetime(2026, 9, 6, 4, 0),  # noqa: DTZ001
        )


@pytest.mark.parametrize(
    ("status", "include_grant"),
    [(ProposalStatus.APPROVED, True), (ProposalStatus.REJECTED, False)],
)
def test_decision_receipt_has_consistent_terminal_grant(
    status: ProposalStatus, include_grant: bool
) -> None:
    receipt = DecisionReceipt(
        proposal_ref=ProposalRef(proposal_id=UUID(int=16), version=1),
        status=status,
        approver_id=UUID(int=2),
        decided_at=NOW,
        reason="reviewed",
        grant=valid_grant() if include_grant else None,
    )

    assert (receipt.grant is not None) is include_grant


@pytest.mark.parametrize(
    ("status", "include_grant"),
    [(ProposalStatus.APPROVED, False), (ProposalStatus.REJECTED, True)],
)
def test_decision_receipt_rejects_inconsistent_terminal_grant(
    status: ProposalStatus, include_grant: bool
) -> None:
    with pytest.raises(ValidationError, match="grant"):
        DecisionReceipt(
            proposal_ref=ProposalRef(proposal_id=UUID(int=16), version=1),
            status=status,
            approver_id=UUID(int=2),
            decided_at=NOW,
            grant=valid_grant() if include_grant else None,
        )


def test_execution_grant_is_not_exported_from_public_package() -> None:
    from commerce_agent import operations

    assert "ExecutionGrant" not in operations.__all__
    assert not hasattr(operations, "ExecutionGrant")
