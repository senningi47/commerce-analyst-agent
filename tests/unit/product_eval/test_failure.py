import pytest

from tests.support.product_eval_failures import (
    CommitOutcomeUnknown,
    DropProposalResponseAfterCommit,
    FailBeforeProposalPersist,
    InjectedFailure,
)
from tests.unit.operations.test_workflow_propose import (
    valid_propose_request,
    workflow_fixture,
)


@pytest.mark.asyncio
async def test_failure_decorator_matches_exact_idempotency_key_only() -> None:
    workflow, inner = workflow_fixture()
    decorated = FailBeforeProposalPersist(inner, once_for="scenario-key-1")
    workflow._store = decorated

    await workflow.propose(valid_propose_request(key="prefix-scenario-key-1-suffix"))
    with pytest.raises(InjectedFailure, match="before_proposal_persist"):
        await workflow.propose(valid_propose_request(key="scenario-key-1"))
    snapshot = await workflow.propose(valid_propose_request(key="scenario-key-1"))

    assert snapshot.status == "pending"
    assert inner.proposal_count == 2


@pytest.mark.asyncio
async def test_after_commit_decorator_drops_only_first_matching_response() -> None:
    workflow, inner = workflow_fixture()
    decorated = DropProposalResponseAfterCommit(inner, once_for="scenario-key-2")
    workflow._store = decorated

    with pytest.raises(CommitOutcomeUnknown, match="proposal_commit_outcome_unknown"):
        await workflow.propose(valid_propose_request(key="scenario-key-2"))
    snapshot = await workflow.propose(valid_propose_request(key="scenario-key-2"))

    assert snapshot.status == "pending"
    assert inner.proposal_count == 1
