"""Evaluation-only failure decorators for reviewed persistence boundaries."""

from commerce_agent.operations._approval import StoredApproval
from commerce_agent.operations._store import ProposalDraft, StoredProposal
from commerce_agent.operations.contracts import (
    ExecutionGrant,
    ExecutionReceipt,
    ProposalSnapshot,
)


class InjectedFailure(RuntimeError):
    pass


class CommitOutcomeUnknown(RuntimeError):
    pass


class _ForwardingStore:
    def __init__(self, inner: object, *, once_for: str) -> None:
        self._inner = inner
        self._once_for = once_for
        self._triggered = False

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def _matches(self, value: object) -> bool:
        if isinstance(value, ProposalDraft):
            identity = value.request.idempotency_key
        elif isinstance(value, StoredProposal):
            identity = value.idempotency_key
        else:
            return False
        return identity == self._once_for


class FailBeforeProposalPersist(_ForwardingStore):
    async def create_or_get_proposal(self, draft: ProposalDraft) -> ProposalSnapshot:
        if not self._triggered and self._matches(draft):
            self._triggered = True
            raise InjectedFailure("before_proposal_persist")
        return await self._inner.create_or_get_proposal(draft)


class DropProposalResponseAfterCommit(_ForwardingStore):
    async def create_or_get_proposal(self, draft: ProposalDraft) -> ProposalSnapshot:
        result = await self._inner.create_or_get_proposal(draft)
        if not self._triggered and self._matches(draft):
            self._triggered = True
            raise CommitOutcomeUnknown("proposal_commit_outcome_unknown")
        return result


class FailBeforeExecuteCall(_ForwardingStore):
    async def execute_once(
        self,
        proposal: StoredProposal,
        approval: StoredApproval,
        grant: ExecutionGrant,
        *,
        now,
    ) -> ExecutionReceipt:
        if not self._triggered and self._matches(proposal):
            self._triggered = True
            raise InjectedFailure("before_execute_call")
        return await self._inner.execute_once(
            proposal, approval, grant, now=now
        )


class DropResponseAfterCommit(_ForwardingStore):
    async def execute_once(
        self,
        proposal: StoredProposal,
        approval: StoredApproval,
        grant: ExecutionGrant,
        *,
        now,
    ) -> ExecutionReceipt:
        receipt = await self._inner.execute_once(
            proposal, approval, grant, now=now
        )
        if not self._triggered and self._matches(proposal):
            self._triggered = True
            raise CommitOutcomeUnknown("operation_commit_outcome_unknown")
        return receipt
