from datetime import UTC, datetime
from uuid import UUID

import pytest

from commerce_agent.operations._approval import (
    ApprovalService,
    FixedClock,
    FixedNonceSource,
    HmacApprovalKeyring,
)
from commerce_agent.operations._memory import InMemoryOperationStore, InvestigationTaskState
from commerce_agent.operations._store import DefaultReferenceValidator
from commerce_agent.operations.commands import TransitionInvestigation
from commerce_agent.operations.contracts import (
    EvidenceRef,
    ExecuteRequest,
    InvestigationStatus,
    InvestigationTaskRef,
    ProposeRequest,
)
from commerce_agent.operations.workflow import OperationWorkflow
from tests.unit.operations.test_workflow_decide import decision_request
from tests.unit.operations.test_workflow_propose import actor

NOW = datetime(2026, 9, 6, 4, 0, tzinfo=UTC)
EVIDENCE = EvidenceRef(kind="investigation_task", ref="task:evidence", digest="d" * 64)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("open", "in_progress"),
        ("open", "blocked"),
        ("in_progress", "blocked"),
        ("blocked", "in_progress"),
        ("in_progress", "resolved"),
        ("blocked", "resolved"),
    ],
)
@pytest.mark.asyncio
async def test_investigation_transition_advances_exact_version(source: str, target: str) -> None:
    task_id = UUID(int=30)
    store = InMemoryOperationStore(
        initial_tasks=(
            InvestigationTaskState(
                task_id=task_id,
                status=InvestigationStatus(source),
                priority="high",
                public_summary="Existing reviewed task.",
                version=3,
            ),
        )
    )
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
    command = TransitionInvestigation(
        type="transition_investigation",
        task_ref=InvestigationTaskRef(task_id=task_id, version=3),
        from_status=source,
        to_status=target,
        reason_code="reviewed_transition",
        evidence_refs=(EVIDENCE,),
        expected_target_version=3,
    )
    proposal = await workflow.propose(
        ProposeRequest(
            actor=actor("analyst", 1),
            command=command,
            evidence_refs=(EVIDENCE,),
            idempotency_key=f"transition-{source}-{target}",
        )
    )
    decision = await workflow.decide(
        decision_request(proposal.proposal_ref, actor("approver", 2), "approve")
    )
    assert decision.grant is not None

    receipt = await workflow.execute(
        ExecuteRequest(actor=actor("approver", 2), grant=decision.grant)
    )

    stored = store.tasks[0]
    assert receipt.before_version + 1 == receipt.after_version
    assert stored.status == target
    assert stored.version == receipt.after_version
