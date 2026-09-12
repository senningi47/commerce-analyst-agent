"""Deep three-method API for controlled Product operations."""

from datetime import timedelta

from commerce_agent.operations._approval import (
    ApprovalService,
    Clock,
    StoredApproval,
    execution_grant_sha256,
)
from commerce_agent.operations._canonical import canonical_command
from commerce_agent.operations._store import OperationStore, ProposalDraft, ReferenceValidator
from commerce_agent.operations.contracts import (
    ActorContext,
    ActorRole,
    DecisionReceipt,
    DecisionRequest,
    ExecuteRequest,
    ExecutionReceipt,
    ProposalRef,
    ProposalSnapshot,
    ProposalStatus,
    ProposeRequest,
)
from commerce_agent.operations.errors import ApprovalError, OperationAuthorizationError


class OperationWorkflow:
    def __init__(
        self,
        *,
        store: OperationStore,
        references: ReferenceValidator,
        clock: Clock,
        approvals: ApprovalService,
    ) -> None:
        self._store = store
        self._references = references
        self._clock = clock
        self._approvals = approvals

    async def propose(self, request: ProposeRequest) -> ProposalSnapshot:
        self._require_role(request.actor, ActorRole.ANALYST, "analyst_required")
        validated = await self._references.validate_command(
            request.command, request.evidence_refs
        )
        preview = await self._store.preview_command(request.command, validated)
        canonical = canonical_command(request.command, preview.target_versions)
        now = self._clock.now()
        return await self._store.create_or_get_proposal(
            ProposalDraft(
                request=request,
                preview=preview,
                payload_sha256=canonical.sha256,
                created_at=now,
                expires_at=now + timedelta(hours=24),
                validated_references=validated,
            )
        )

    async def decide(self, request: DecisionRequest) -> DecisionReceipt:
        self._require_role(request.actor, ActorRole.APPROVER, "approver_required")
        proposal = await self._require_proposal(request.proposal_ref)
        if proposal.requester_id == request.actor.actor_id:
            raise OperationAuthorizationError("self_approval_denied", retryable=False)

        approval: StoredApproval | None = None
        grant = None
        if request.decision == "approve":
            grant, nonce_digest = self._approvals.issue_grant(
                proposal_ref=proposal.snapshot.proposal_ref,
                payload_sha256=proposal.snapshot.payload_sha256,
                requester_id=proposal.requester_id,
                approver=request.actor,
                target_versions=proposal.snapshot.preview.target_versions,
            )
            approval = StoredApproval(
                proposal_ref=proposal.snapshot.proposal_ref,
                payload_sha256=proposal.snapshot.payload_sha256,
                requester_id=proposal.requester_id,
                approver_id=request.actor.actor_id,
                target_versions=proposal.snapshot.preview.target_versions,
                expires_at=grant.expires_at,
                nonce_digest=nonce_digest,
                key_version=grant.key_version,
            )
        receipt = DecisionReceipt(
            proposal_ref=request.proposal_ref,
            status=(
                ProposalStatus.APPROVED
                if request.decision == "approve"
                else ProposalStatus.REJECTED
            ),
            approver_id=request.actor.actor_id,
            decided_at=self._clock.now(),
            reason=request.reason,
            grant=grant,
        )
        return await self._store.decide_once(receipt, approval, now=self._clock.now())

    async def execute(self, request: ExecuteRequest) -> ExecutionReceipt:
        self._require_role(request.actor, ActorRole.APPROVER, "approver_required")
        proposal_ref = ProposalRef(
            proposal_id=request.grant.proposal_id,
            version=request.grant.proposal_version,
        )
        proposal = await self._require_proposal(proposal_ref)
        decision = await self._store.read_decision(proposal_ref)
        if (
            decision is None
            or decision.status is not ProposalStatus.APPROVED
            or decision.approval is None
            or decision.grant_sha256 is None
        ):
            raise ApprovalError("approval_required", retryable=False)
        if (
            request.actor.actor_id != decision.approver_id
            or request.grant.approver_id != decision.approver_id
        ):
            raise OperationAuthorizationError("grant_actor_mismatch", retryable=False)
        existing = await self._store.read_execution(proposal_ref)
        if (
            existing is not None
            and execution_grant_sha256(request.grant) == decision.grant_sha256
        ):
            return existing
        self._approvals.verify_grant(
            request.grant,
            stored=decision.approval,
            actor=request.actor,
        )
        return await self._store.execute_once(
            proposal,
            decision.approval,
            request.grant,
            now=self._clock.now(),
        )

    async def _require_proposal(self, proposal_ref: ProposalRef):
        proposal = await self._store.read_proposal(proposal_ref)
        if proposal is None:
            raise ApprovalError("proposal_not_found", retryable=False)
        return proposal

    @staticmethod
    def _require_role(actor: ActorContext, role: ActorRole, reason_code: str) -> None:
        if actor.role is not role:
            raise OperationAuthorizationError(reason_code, retryable=False)
