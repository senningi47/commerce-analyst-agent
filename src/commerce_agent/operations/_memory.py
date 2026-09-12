"""Atomic in-memory adapter for the controlled-operation workflow."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from commerce_agent.operations._approval import StoredApproval, execution_grant_sha256
from commerce_agent.operations._store import (
    ProposalDraft,
    StoredDecision,
    StoredProposal,
    ValidatedReferences,
)
from commerce_agent.operations.commands import (
    AddInvestigationConclusion,
    AssignInvestigation,
    CloseInvestigation,
    CreateAndEnableMetricAlertRule,
    CreateInvestigationFromAlertHit,
    CreateInvestigationTask,
    OpenSellerRiskCase,
    OperationCommand,
    TransitionInvestigation,
)
from commerce_agent.operations.contracts import (
    CommandPreview,
    DecisionReceipt,
    ExecutionGrant,
    ExecutionReceipt,
    ExecutionStatus,
    InvestigationStatus,
    ProposalRef,
    ProposalSnapshot,
    ProposalStatus,
)
from commerce_agent.operations.errors import (
    ApprovalError,
    OperationConflictError,
    OperationContractError,
)


@dataclass(frozen=True)
class InvestigationTaskState:
    task_id: UUID
    status: InvestigationStatus
    priority: str
    public_summary: str
    version: int
    assignee_ref: str | None = None
    conclusion_ref: str | None = None
    linked_risk_id: UUID | None = None


@dataclass(frozen=True)
class RiskAnnotationState:
    risk_id: UUID
    status: str
    version: int


@dataclass(frozen=True)
class MetricAlertRuleState:
    rule_id: UUID
    status: str
    version: int


class InMemoryOperationStore:
    def __init__(self, *, initial_tasks: tuple[InvestigationTaskState, ...] = ()) -> None:
        self._lock = asyncio.Lock()
        self._proposals: dict[tuple[UUID, int], StoredProposal] = {}
        self._latest_versions: dict[UUID, int] = {}
        self._idempotency: dict[tuple[UUID, str], tuple[ProposalRef, str, str]] = {}
        self._decisions: dict[tuple[UUID, int], StoredDecision] = {}
        self._decision_receipts: dict[tuple[UUID, int], DecisionReceipt] = {}
        self._executions: dict[tuple[UUID, int], ExecutionReceipt] = {}
        self._nonce_claims: dict[str, ProposalRef] = {}
        self._tasks = {task.task_id: task for task in initial_tasks}
        self._risks: dict[UUID, RiskAnnotationState] = {}
        self._rules: dict[UUID, MetricAlertRuleState] = {}
        self.business_write_count = 0
        self.audit_count = 0
        self.nonce_claim_count = 0

    @property
    def proposal_count(self) -> int:
        return len(self._proposals)

    @property
    def tasks(self) -> tuple[InvestigationTaskState, ...]:
        return tuple(self._tasks.values())

    @property
    def risks(self) -> tuple[RiskAnnotationState, ...]:
        return tuple(self._risks.values())

    @property
    def rules(self) -> tuple[MetricAlertRuleState, ...]:
        return tuple(self._rules.values())

    async def preview_command(
        self, command: OperationCommand, validated: ValidatedReferences
    ) -> CommandPreview:
        del validated
        if isinstance(command, OpenSellerRiskCase):
            versions = {"investigation_task": 0, "risk_annotation": 0}
            affected_rows = 2
            summary = "Create a seller-risk annotation and linked investigation task."
        elif isinstance(command, (CreateInvestigationTask, CreateInvestigationFromAlertHit)):
            versions = {"investigation_task": 0}
            affected_rows = 1
            summary = "Create one investigation task."
        elif isinstance(command, CreateAndEnableMetricAlertRule):
            versions = {"metric_alert_rule": 0}
            affected_rows = 1
            summary = "Create one enabled metric alert rule."
        else:
            task = self._tasks.get(command.task_ref.task_id)
            if task is None:
                raise OperationContractError("investigation_not_found", retryable=False)
            if task.version != command.expected_target_version:
                raise OperationConflictError("target_version_conflict", retryable=False)
            versions = {"investigation_task": task.version}
            affected_rows = 1
            summary = "Update one investigation task at its exact version."
        return CommandPreview(
            command_type=command.type,
            title=getattr(command, "title", command.type.replace("_", " ").title()),
            public_summary=summary,
            target_versions=versions,
            affected_rows=affected_rows,
        )

    async def create_or_get_proposal(self, draft: ProposalDraft) -> ProposalSnapshot:
        request = draft.request
        identity = (request.actor.actor_id, request.idempotency_key)
        async with self._lock:
            existing_binding = self._idempotency.get(identity)
            if existing_binding is not None:
                ref, command_type, payload_sha256 = existing_binding
                if (command_type, payload_sha256) != (
                    request.command.type,
                    draft.payload_sha256,
                ):
                    raise OperationConflictError("idempotency_conflict", retryable=False)
                return self._proposals[(ref.proposal_id, ref.version)].snapshot

            if request.revises is None:
                proposal_ref = ProposalRef(proposal_id=uuid4(), version=1)
            else:
                old_key = (request.revises.proposal_id, request.revises.version)
                previous = self._proposals.get(old_key)
                latest = self._latest_versions.get(request.revises.proposal_id)
                if (
                    previous is None
                    or previous.requester_id != request.actor.actor_id
                    or previous.snapshot.status is not ProposalStatus.PENDING
                    or latest != request.revises.version
                ):
                    raise OperationConflictError("proposal_revision_conflict", retryable=False)
                previous.snapshot = previous.snapshot.model_copy(
                    update={"status": ProposalStatus.SUPERSEDED}
                )
                proposal_ref = ProposalRef(
                    proposal_id=request.revises.proposal_id,
                    version=request.revises.version + 1,
                )

            snapshot = ProposalSnapshot(
                proposal_ref=proposal_ref,
                status=ProposalStatus.PENDING,
                requester_id=request.actor.actor_id,
                command_type=request.command.type,
                payload_sha256=draft.payload_sha256,
                preview=draft.preview,
                created_at=draft.created_at,
                expires_at=draft.expires_at,
            )
            stored = StoredProposal(
                snapshot=snapshot,
                command=request.command,
                requester_id=request.actor.actor_id,
                evidence_refs=request.evidence_refs,
                idempotency_key=request.idempotency_key,
                revises=request.revises,
                validated_references=draft.validated_references,
            )
            key = (proposal_ref.proposal_id, proposal_ref.version)
            self._proposals[key] = stored
            self._latest_versions[proposal_ref.proposal_id] = proposal_ref.version
            self._idempotency[identity] = (
                proposal_ref,
                request.command.type,
                draft.payload_sha256,
            )
            return snapshot

    async def read_proposal(self, proposal_ref: ProposalRef) -> StoredProposal | None:
        return self._proposals.get((proposal_ref.proposal_id, proposal_ref.version))

    async def status(self, proposal_ref: ProposalRef) -> ProposalStatus | None:
        stored = await self.read_proposal(proposal_ref)
        return stored.snapshot.status if stored else None

    async def decide_once(
        self,
        receipt: DecisionReceipt,
        approval: StoredApproval | None,
        *,
        now: datetime,
    ) -> DecisionReceipt:
        key = (receipt.proposal_ref.proposal_id, receipt.proposal_ref.version)
        async with self._lock:
            proposal = self._proposals.get(key)
            if proposal is None:
                raise ApprovalError("proposal_not_found", retryable=False)
            if proposal.snapshot.status is not ProposalStatus.PENDING:
                raise ApprovalError("proposal_already_decided", retryable=False)
            if now >= proposal.snapshot.expires_at:
                proposal.snapshot = proposal.snapshot.model_copy(
                    update={"status": ProposalStatus.EXPIRED}
                )
                raise ApprovalError("proposal_expired", retryable=False)
            proposal.snapshot = proposal.snapshot.model_copy(update={"status": receipt.status})
            self._decisions[key] = StoredDecision(
                status=receipt.status,
                approver_id=receipt.approver_id,
                decided_at=receipt.decided_at,
                reason=receipt.reason,
                approval=approval,
                grant_sha256=(
                    execution_grant_sha256(receipt.grant)
                    if receipt.grant is not None
                    else None
                ),
            )
            self._decision_receipts[key] = receipt
            return receipt

    async def decision(self, proposal_ref: ProposalRef) -> DecisionReceipt | None:
        return self._decision_receipts.get(
            (proposal_ref.proposal_id, proposal_ref.version)
        )

    async def read_decision(self, proposal_ref: ProposalRef) -> StoredDecision | None:
        return self._decisions.get((proposal_ref.proposal_id, proposal_ref.version))

    async def read_execution(self, proposal_ref: ProposalRef) -> ExecutionReceipt | None:
        return self._executions.get((proposal_ref.proposal_id, proposal_ref.version))

    async def execute_once(
        self,
        proposal: StoredProposal,
        approval: StoredApproval,
        grant: ExecutionGrant,
        *,
        now: datetime,
    ) -> ExecutionReceipt:
        ref = proposal.snapshot.proposal_ref
        key = (ref.proposal_id, ref.version)
        async with self._lock:
            previous = self._executions.get(key)
            if previous is not None:
                return previous
            claimed_by = self._nonce_claims.get(approval.nonce_digest)
            if claimed_by is not None:
                raise ApprovalError("nonce_used", retryable=False)

            before_version, after_version = self._apply_command(proposal.command, ref)
            receipt = ExecutionReceipt(
                execution_id=uuid5(NAMESPACE_URL, f"operation:{ref.proposal_id}:{ref.version}"),
                proposal_ref=ref,
                status=ExecutionStatus.SUCCEEDED,
                command_type=proposal.command.type,
                before_version=before_version,
                after_version=after_version,
                committed_at=now,
                public_summary=proposal.snapshot.preview.public_summary,
                audit_ref=f"audit:{ref.proposal_id}:{ref.version}",
            )
            self._nonce_claims[approval.nonce_digest] = ref
            self._executions[key] = receipt
            self.business_write_count += 1
            self.audit_count += 1
            self.nonce_claim_count += 1
            return receipt

    def _apply_command(self, command: OperationCommand, ref: ProposalRef) -> tuple[int, int]:
        if isinstance(command, (CreateInvestigationTask, CreateInvestigationFromAlertHit)):
            task_id = uuid5(NAMESPACE_URL, f"task:{ref.proposal_id}:{ref.version}")
            summary = getattr(command, "public_summary", "Created from a reviewed alert hit.")
            self._tasks[task_id] = InvestigationTaskState(
                task_id=task_id,
                status=InvestigationStatus.OPEN,
                priority=command.priority,
                public_summary=summary,
                version=1,
            )
            return 0, 1
        if isinstance(command, OpenSellerRiskCase):
            task_id = uuid5(NAMESPACE_URL, f"task:{ref.proposal_id}:{ref.version}")
            risk_id = uuid5(NAMESPACE_URL, f"risk:{ref.proposal_id}:{ref.version}")
            self._tasks[task_id] = InvestigationTaskState(
                task_id=task_id,
                status=InvestigationStatus.OPEN,
                priority=command.priority,
                public_summary="Seller risk requires investigation.",
                version=1,
                linked_risk_id=risk_id,
            )
            self._risks[risk_id] = RiskAnnotationState(
                risk_id=risk_id, status="under_investigation", version=1
            )
            return 0, 1
        if isinstance(command, CreateAndEnableMetricAlertRule):
            rule_id = uuid5(NAMESPACE_URL, f"rule:{ref.proposal_id}:{ref.version}")
            self._rules[rule_id] = MetricAlertRuleState(
                rule_id=rule_id, status="enabled", version=1
            )
            return 0, 1

        task = self._tasks.get(command.task_ref.task_id)
        if task is None:
            raise OperationContractError("investigation_not_found", retryable=False)
        if task.version != command.expected_target_version:
            raise OperationConflictError("target_version_conflict", retryable=False)
        updated = task
        if isinstance(command, AssignInvestigation):
            updated = replace(task, assignee_ref=command.assignee_ref, version=task.version + 1)
        elif isinstance(command, TransitionInvestigation):
            if task.status is not command.from_status:
                raise OperationConflictError("investigation_status_conflict", retryable=False)
            updated = replace(task, status=command.to_status, version=task.version + 1)
        elif isinstance(command, AddInvestigationConclusion):
            if task.status is not InvestigationStatus.RESOLVED:
                raise OperationContractError("investigation_not_resolved", retryable=False)
            conclusion_ref = f"conclusion:{ref.proposal_id}:{ref.version}"
            updated = replace(task, conclusion_ref=conclusion_ref, version=task.version + 1)
        elif isinstance(command, CloseInvestigation):
            if task.status is not InvestigationStatus.RESOLVED:
                raise OperationContractError("investigation_not_resolved", retryable=False)
            if task.conclusion_ref != command.conclusion_ref:
                raise OperationContractError("conclusion_required", retryable=False)
            if task.linked_risk_id is not None and command.risk_disposition is None:
                raise OperationContractError("risk_disposition_required", retryable=False)
            updated = replace(task, status=InvestigationStatus.CLOSED, version=task.version + 1)
            if task.linked_risk_id is not None:
                risk = self._risks[task.linked_risk_id]
                self._risks[task.linked_risk_id] = replace(
                    risk, status=command.risk_disposition.value, version=risk.version + 1
                )
        self._tasks[task.task_id] = updated
        return task.version, updated.version
