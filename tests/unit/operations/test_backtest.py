from datetime import UTC, datetime
from decimal import Decimal

import pytest

from commerce_agent.operations._approval import (
    ApprovalService,
    FixedClock,
    FixedNonceSource,
    HmacApprovalKeyring,
)
from commerce_agent.operations._backtest import (
    AlertObservation,
    InMemoryAlertObservationPort,
    InMemoryBacktestRegistry,
    MetricAlertBacktester,
    StaticMetricDefinitionPort,
    rule_spec_sha256,
)
from commerce_agent.operations._memory import InMemoryOperationStore
from commerce_agent.operations._store import DefaultReferenceValidator
from commerce_agent.operations.commands import CreateAndEnableMetricAlertRule
from commerce_agent.operations.contracts import (
    AlertBacktestRequest,
    AlertComparator,
    AlertMetric,
    CalendarWindow,
    EvidenceRef,
    MetricRef,
    ProposeRequest,
)
from commerce_agent.operations.errors import OperationContractError
from commerce_agent.operations.workflow import OperationWorkflow
from tests.unit.operations.test_workflow_propose import actor


def observation(
    month: int, *, numerator: int, denominator: int, complete: bool
) -> AlertObservation:
    next_month = month + 1
    return AlertObservation(
        window_started_at=datetime(2026, month, 1, tzinfo=UTC),
        window_ended_at=datetime(2026, next_month, 1, tzinfo=UTC),
        numerator=numerator,
        denominator=denominator,
        complete=complete,
    )


def backtest_request(**updates: object) -> AlertBacktestRequest:
    payload: dict[str, object] = {
        "metric": AlertMetric.LATE_DELIVERY_RATE,
        "metric_revision": "metric.late_delivery_rate.v1",
        "grain": "global",
        "window": CalendarWindow.MONTH,
        "comparator": AlertComparator.GREATER_THAN,
        "threshold": Decimal("0.2000"),
        "minimum_denominator": 5,
        "started_at": datetime(2026, 1, 1, tzinfo=UTC),
        "ended_at": datetime(2026, 4, 1, tzinfo=UTC),
        "filter_refs": (),
    }
    return AlertBacktestRequest.model_validate(payload | updates)


@pytest.mark.asyncio
async def test_backtest_marks_low_sample_and_incomplete_windows_as_excluded() -> None:
    backtester = MetricAlertBacktester(
        metrics=StaticMetricDefinitionPort(),
        observations=InMemoryAlertObservationPort(
            (
                observation(1, numerator=1, denominator=2, complete=True),
                observation(2, numerator=8, denominator=10, complete=False),
                observation(3, numerator=3, denominator=10, complete=True),
            )
        ),
    )

    result = await backtester.run(backtest_request())

    assert [item.excluded_reason for item in result.windows] == [
        "below_minimum_denominator",
        "incomplete_window",
        None,
    ]
    assert [item.hit for item in result.windows] == [False, False, True]


def test_rule_spec_hash_changes_for_every_semantic_input() -> None:
    baseline = backtest_request()
    baseline_hash = rule_spec_sha256(baseline)
    for changed in (
        baseline.model_copy(update={"threshold": Decimal("0.2100")}),
        baseline.model_copy(update={"minimum_denominator": 20}),
        baseline.model_copy(update={"window": CalendarWindow.WEEK}),
        baseline.model_copy(update={"metric_revision": "metric.late_delivery_rate.v2"}),
    ):
        assert rule_spec_sha256(changed) != baseline_hash


@pytest.mark.asyncio
async def test_changed_alert_rule_requires_new_backtest_and_new_approval() -> None:
    request = backtest_request()
    snapshot = await MetricAlertBacktester(
        metrics=StaticMetricDefinitionPort(),
        observations=InMemoryAlertObservationPort(
            (observation(1, numerator=3, denominator=10, complete=True),)
        ),
    ).run(request)
    store = InMemoryOperationStore()
    workflow = OperationWorkflow(
        store=store,
        references=DefaultReferenceValidator(
            backtests=InMemoryBacktestRegistry((snapshot,))
        ),
        clock=FixedClock(request.ended_at),
        approvals=ApprovalService(
            clock=FixedClock(request.ended_at),
            nonce_source=FixedNonceSource(bytes(range(32))),
            keyring=HmacApprovalKeyring({1: b"day4-test-key"}, active_version=1),
        ),
    )
    evidence = EvidenceRef(
        kind="alert_backtest",
        ref=f"backtest:{snapshot.backtest_ref.backtest_id}",
        digest=snapshot.backtest_ref.rule_spec_sha256,
    )
    changed = CreateAndEnableMetricAlertRule(
        type="create_and_enable_metric_alert_rule",
        alert_backtest_ref=snapshot.backtest_ref,
        metric_ref=MetricRef(name=request.metric.value, revision=request.metric_revision),
        grain=request.grain,
        window=request.window.value,
        comparator=request.comparator.value,
        threshold=Decimal("0.2500"),
        minimum_denominator=request.minimum_denominator,
        filter_refs=request.filter_refs,
        evidence_refs=(evidence,),
        expected_target_version=0,
    )

    with pytest.raises(OperationContractError) as caught:
        await workflow.propose(
            ProposeRequest(
                actor=actor(),
                command=changed,
                evidence_refs=(evidence,),
                idempotency_key="changed-alert-rule",
            )
        )

    assert caught.value.reason_code == "backtest_spec_mismatch"
    assert store.proposal_count == 0
