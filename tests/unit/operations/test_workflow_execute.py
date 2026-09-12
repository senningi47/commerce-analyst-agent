from datetime import UTC, datetime

import pytest

from commerce_agent.operations.contracts import ExecuteRequest
from tests.unit.operations.test_workflow_decide import decision_request
from tests.unit.operations.test_workflow_propose import (
    actor,
    valid_propose_request,
    workflow_fixture,
)

NOW = datetime(2026, 9, 6, 4, 0, tzinfo=UTC)


async def approved_workflow_fixture():
    workflow, store = workflow_fixture()
    proposal = await workflow.propose(valid_propose_request())
    decision = await workflow.decide(
        decision_request(proposal.proposal_ref, actor("approver", 2), "approve")
    )
    assert decision.grant is not None
    return workflow, store, decision.grant


@pytest.mark.asyncio
async def test_execute_retry_returns_original_receipt_without_second_write() -> None:
    workflow, store, grant = await approved_workflow_fixture()
    request = ExecuteRequest(actor=actor("approver", 2), grant=grant)

    first = await workflow.execute(request)
    repeated = await workflow.execute(request)

    assert repeated == first
    assert store.business_write_count == 1
    assert store.audit_count == 1
    assert store.nonce_claim_count == 1
    assert store.tasks[0].status == "open"
    assert store.tasks[0].version == 1
