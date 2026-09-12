"""Internal high-level persistence contracts for controlled operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from commerce_agent.operations._approval import StoredApproval
from commerce_agent.operations.commands import (
    CreateAndEnableMetricAlertRule,
    OpenSellerRiskCase,
    OperationCommand,
)
from commerce_agent.operations.contracts import (
    CommandPreview,
    DecisionReceipt,
    EvidenceRef,
    ExecutionGrant,
    ExecutionReceipt,
    ProposalRef,
    ProposalSnapshot,
    ProposalStatus,
    ProposeRequest,
)
from commerce_agent.operations.errors import OperationContractError


@dataclass(frozen=True)
class ValidatedReferences:
    evidence_refs: tuple[EvidenceRef, ...]
    private_bindings: tuple[object, ...] = ()


class ReferenceValidator(Protocol):
    async def validate_command(
        self, command: OperationCommand, evidence_refs: tuple[EvidenceRef, ...]
    ) -> ValidatedReferences: ...


class SellerReferenceResolver(Protocol):
    async def resolve(self, reference, *, expected_evidence_digest: str): ...


class BacktestResolver(Protocol):
    async def resolve(self, reference): ...


class DefaultReferenceValidator:
    def __init__(
        self,
        *,
        seller_resolver: SellerReferenceResolver | None = None,
        backtests: BacktestResolver | None = None,
    ) -> None:
        self._seller_resolver = seller_resolver
        self._backtests = backtests

    async def validate_command(
        self, command: OperationCommand, evidence_refs: tuple[EvidenceRef, ...]
    ) -> ValidatedReferences:
        requested = {(item.kind, item.ref, item.digest) for item in evidence_refs}
        commanded = {(item.kind, item.ref, item.digest) for item in command.evidence_refs}
        if requested != commanded:
            raise OperationContractError("evidence_binding_mismatch", retryable=False)
        private_bindings: tuple[object, ...] = ()
        if isinstance(command, OpenSellerRiskCase):
            if self._seller_resolver is None:
                raise OperationContractError(
                    "seller_ref_validation_unavailable", retryable=False
                )
            query_evidence = tuple(item for item in evidence_refs if item.kind == "query")
            if len(query_evidence) != 1:
                raise OperationContractError("seller_query_evidence_required", retryable=False)
            target = await self._seller_resolver.resolve(
                command.seller_ref,
                expected_evidence_digest=query_evidence[0].digest,
            )
            private_bindings = (target,)
        if isinstance(command, CreateAndEnableMetricAlertRule):
            if self._backtests is None:
                raise OperationContractError(
                    "backtest_validation_unavailable", retryable=False
                )
            snapshot = await self._backtests.resolve(command.alert_backtest_ref)
            if snapshot is None:
                raise OperationContractError("backtest_not_found", retryable=False)
            backtest = snapshot.request
            expected = (
                snapshot.backtest_ref,
                backtest.metric.value,
                backtest.metric_revision,
                backtest.grain,
                backtest.window.value,
                backtest.comparator.value,
                backtest.threshold,
                backtest.minimum_denominator,
                tuple(sorted(backtest.filter_refs)),
            )
            actual = (
                command.alert_backtest_ref,
                command.metric_ref.name,
                command.metric_ref.revision,
                command.grain,
                command.window,
                command.comparator,
                command.threshold,
                command.minimum_denominator,
                tuple(sorted(command.filter_refs)),
            )
            if actual != expected:
                raise OperationContractError("backtest_spec_mismatch", retryable=False)
            private_bindings += (snapshot,)
        return ValidatedReferences(
            evidence_refs=evidence_refs, private_bindings=private_bindings
        )


@dataclass(frozen=True)
class ProposalDraft:
    request: ProposeRequest
    preview: CommandPreview
    payload_sha256: str
    created_at: datetime
    expires_at: datetime
    validated_references: ValidatedReferences


@dataclass
class StoredProposal:
    snapshot: ProposalSnapshot
    command: OperationCommand
    requester_id: UUID
    evidence_refs: tuple[EvidenceRef, ...]
    idempotency_key: str
    revises: ProposalRef | None
    validated_references: ValidatedReferences


@dataclass(frozen=True)
class StoredDecision:
    status: ProposalStatus
    approver_id: UUID
    decided_at: datetime
    reason: str | None
    approval: StoredApproval | None
    grant_sha256: str | None


class OperationStore(Protocol):
    async def preview_command(
        self, command: OperationCommand, validated: ValidatedReferences
    ) -> CommandPreview: ...

    async def create_or_get_proposal(self, draft: ProposalDraft) -> ProposalSnapshot: ...

    async def read_proposal(self, proposal_ref: ProposalRef) -> StoredProposal | None: ...

    async def decide_once(
        self,
        receipt: DecisionReceipt,
        approval: StoredApproval | None,
        *,
        now: datetime,
    ) -> DecisionReceipt: ...

    async def read_decision(self, proposal_ref: ProposalRef) -> StoredDecision | None: ...

    async def execute_once(
        self,
        proposal: StoredProposal,
        approval: StoredApproval,
        grant: ExecutionGrant,
        *,
        now: datetime,
    ) -> ExecutionReceipt: ...

    async def read_execution(self, proposal_ref: ProposalRef) -> ExecutionReceipt | None: ...
