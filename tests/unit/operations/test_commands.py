from datetime import UTC, datetime
from decimal import Decimal
from typing import get_args
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

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
    AlertBacktestRef,
    AlertHitRef,
    EvidenceRef,
    InvestigationStatus,
    InvestigationTaskRef,
    MetricRef,
    RiskDisposition,
    SellerRef,
)

NOW = datetime(2026, 9, 6, 4, 0, tzinfo=UTC)
EVIDENCE = EvidenceRef(kind="query", ref="query:evidence-1", digest="a" * 64)
TASK_REF = InvestigationTaskRef(task_id=UUID(int=20), version=3)


def valid_create_task() -> CreateInvestigationTask:
    return CreateInvestigationTask(
        type="create_investigation_task",
        title="Review delayed deliveries",
        priority="high",
        public_summary="Validate the affected cohort and document findings.",
        evidence_refs=(EVIDENCE,),
        expected_target_version=0,
    )


def valid_transition_payload() -> dict[str, object]:
    return {
        "type": "transition_investigation",
        "task_ref": TASK_REF,
        "from_status": InvestigationStatus.OPEN,
        "to_status": InvestigationStatus.IN_PROGRESS,
        "reason_code": "analysis_started",
        "evidence_refs": (EVIDENCE,),
        "expected_target_version": 3,
    }


@pytest.mark.parametrize(
    "payload_update",
    [
        {"unknown": "field"},
        {"expected_target_version": 1},
        {"evidence_refs": ()},
    ],
)
def test_create_task_fails_closed(payload_update: dict[str, object]) -> None:
    payload = valid_create_task().model_dump(mode="python") | payload_update
    with pytest.raises(ValidationError):
        CreateInvestigationTask.model_validate(payload)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("open", "closed"),
        ("blocked", "closed"),
        ("resolved", "in_progress"),
        ("closed", "open"),
    ],
)
def test_transition_command_rejects_forbidden_edges(source: str, target: str) -> None:
    with pytest.raises(ValidationError, match="transition"):
        TransitionInvestigation.model_validate(
            valid_transition_payload() | {"from_status": source, "to_status": target}
        )


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("open", "in_progress"),
        ("open", "blocked"),
        ("in_progress", "blocked"),
        ("in_progress", "resolved"),
        ("blocked", "in_progress"),
        ("blocked", "resolved"),
    ],
)
def test_transition_command_accepts_only_reviewed_edges(source: str, target: str) -> None:
    command = TransitionInvestigation.model_validate(
        valid_transition_payload() | {"from_status": source, "to_status": target}
    )

    assert command.to_status == target


def test_all_eight_command_shapes_are_discriminated() -> None:
    commands = (
        OpenSellerRiskCase(
            type="open_seller_risk_case",
            seller_ref=SellerRef(namespace="product:seller-target", token="opaque-token"),
            observation_started_at=NOW,
            observation_ended_at=NOW.replace(hour=5),
            metric_ref=MetricRef(name="late_delivery_rate", revision="v1"),
            numerator=3,
            denominator=10,
            observed=Decimal("0.3000"),
            threshold=Decimal("0.2000"),
            title="Investigate seller delivery risk",
            priority="high",
            evidence_refs=(EVIDENCE,),
            expected_target_version=0,
        ),
        valid_create_task(),
        CreateInvestigationFromAlertHit(
            type="create_investigation_from_alert_hit",
            alert_hit_ref=AlertHitRef(hit_id=UUID(int=22), evidence_digest="b" * 64),
            title="Investigate alert hit",
            priority="medium",
            evidence_refs=(EVIDENCE,),
            expected_target_version=0,
        ),
        CreateAndEnableMetricAlertRule(
            type="create_and_enable_metric_alert_rule",
            alert_backtest_ref=AlertBacktestRef(
                backtest_id=UUID(int=23), rule_spec_sha256="c" * 64
            ),
            metric_ref=MetricRef(name="late_delivery_rate", revision="v1"),
            grain="global",
            window="month",
            comparator="greater_than",
            threshold=Decimal("0.2000"),
            minimum_denominator=10,
            filter_refs=(),
            evidence_refs=(EVIDENCE,),
            expected_target_version=0,
        ),
        AssignInvestigation(
            type="assign_investigation",
            task_ref=TASK_REF,
            assignee_ref="analyst:on-call",
            evidence_refs=(EVIDENCE,),
            expected_target_version=3,
        ),
        TransitionInvestigation.model_validate(valid_transition_payload()),
        AddInvestigationConclusion(
            type="add_investigation_conclusion",
            task_ref=TASK_REF,
            conclusion_code="delivery_capacity_issue",
            conclusion_summary="Capacity constraints are supported by the reviewed evidence.",
            evidence_refs=(EVIDENCE,),
            expected_target_version=3,
        ),
        CloseInvestigation(
            type="close_investigation",
            task_ref=TASK_REF,
            conclusion_ref="conclusion:reviewed-1",
            risk_disposition=RiskDisposition.CONFIRMED,
            evidence_refs=(EVIDENCE,),
            expected_target_version=3,
        ),
    )
    adapter = TypeAdapter(OperationCommand)

    assert len(get_args(get_args(OperationCommand)[0])) == 8
    assert tuple(adapter.validate_python(item.model_dump()).type for item in commands) == tuple(
        item.type for item in commands
    )


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_open_seller_risk_case_rejects_non_finite_decimals(value: Decimal) -> None:
    base = {
        "type": "open_seller_risk_case",
        "seller_ref": SellerRef(namespace="product:seller-target", token="opaque-token"),
        "observation_started_at": NOW,
        "observation_ended_at": NOW.replace(hour=5),
        "metric_ref": MetricRef(name="late_delivery_rate", revision="v1"),
        "numerator": 3,
        "denominator": 10,
        "observed": value,
        "threshold": Decimal("0.2000"),
        "title": "Investigate seller delivery risk",
        "priority": "high",
        "evidence_refs": (EVIDENCE,),
        "expected_target_version": 0,
    }
    with pytest.raises(ValidationError, match="finite"):
        OpenSellerRiskCase.model_validate(base)


def test_task_ref_version_must_match_command_target_version() -> None:
    with pytest.raises(ValidationError, match="version"):
        AssignInvestigation(
            type="assign_investigation",
            task_ref=TASK_REF,
            assignee_ref="analyst:on-call",
            evidence_refs=(EVIDENCE,),
            expected_target_version=2,
        )
