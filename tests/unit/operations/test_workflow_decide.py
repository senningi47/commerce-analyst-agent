from datetime import UTC, datetime

import pytest

from commerce_agent.operations.contracts import DecisionRequest
from commerce_agent.operations.errors import ApprovalError, OperationAuthorizationError
from tests.unit.operations.test_workflow_propose import (
    actor,
    valid_propose_request,
    workflow_fixture,
)

NOW = datetime(2026, 9, 6, 4, 0, tzinfo=UTC)


def decision_request(proposal_ref, approver, decision: str) -> DecisionRequest:
    return DecisionRequest(
        actor=approver,
        proposal_ref=proposal_ref,
        decision=decision,
        reason="reviewed evidence",
    )


@pytest.mark.asyncio
async def test_first_decision_wins_and_self_approval_never_creates_grant() -> None:
    workflow, store = workflow_fixture()
    proposal = await workflow.propose(
        valid_propose_request(actor_context=actor("analyst", 1))
    )

    with pytest.raises(OperationAuthorizationError) as caught:
        await workflow.decide(
            decision_request(proposal.proposal_ref, actor("approver", 1), "approve")
        )
    assert caught.value.reason_code == "self_approval_denied"
    assert await store.decision(proposal.proposal_ref) is None

    receipt = await workflow.decide(
        decision_request(proposal.proposal_ref, actor("approver", 2), "approve")
    )
    assert receipt.grant is not None
    with pytest.raises(ApprovalError) as duplicate:
        await workflow.decide(
            decision_request(proposal.proposal_ref, actor("approver", 3), "reject")
        )
    assert duplicate.value.reason_code == "proposal_already_decided"


@pytest.mark.asyncio
async def test_rejection_is_terminal_and_never_returns_a_grant() -> None:
    workflow, _store = workflow_fixture()
    proposal = await workflow.propose(valid_propose_request())

    receipt = await workflow.decide(
        decision_request(proposal.proposal_ref, actor("approver", 2), "reject")
    )

    assert receipt.status == "rejected"
    assert receipt.grant is None
