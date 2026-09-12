from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

import pytest
from pydantic import ValidationError

from commerce_agent.context_builder.contracts import ContextDatum, DataNamespace, DatumKind
from commerce_agent.model.contracts import CostUnavailable, RunScope, UsageUnavailable
from commerce_agent.orchestration.contracts import (
    AskUserCandidate,
    BirdARunOutcome,
    BirdARunRequest,
    BirdCRequest,
    BirdCResponse,
    ClarificationItem,
    ClarificationRequest,
    ConfirmedInvestigationFacts,
    InvestigationClaim,
    InvestigationEvidence,
    InvestigationPlan,
    InvestigationReport,
    InvestigationTimeRange,
    QueryPlanStep,
    RetailRunOutcome,
    RetailRunRequest,
    StopKind,
    StopOutcome,
    SubmitSqlCandidate,
)

ATTEMPT_ID = UUID("00000000-0000-0000-0000-000000000002")


def bird_scope(*, mode: str = "a") -> RunScope:
    return RunScope(
        run_id=UUID("00000000-0000-0000-0000-000000000001"),
        track="bird",
        mode=mode,
        subject_id="synthetic",
        experiment_id="day3",
        config_hash="a" * 64,
    )


def user_datum(content: str) -> ContextDatum:
    return ContextDatum(
        kind=DatumKind.USER_INPUT,
        namespace=DataNamespace.USER_INPUT,
        source_ref="user:current",
        revision=None,
        content=content,
        digest=sha256(content.encode("utf-8")).hexdigest(),
    )


def test_retail_request_rejects_bird_scope() -> None:
    with pytest.raises(ValidationError, match="retail scope"):
        RetailRunRequest(
            run_scope=bird_scope(mode="a"),
            attempt_id=ATTEMPT_ID,
            current_input=user_datum("question"),
        )


def test_bird_a_request_rejects_retail_scope() -> None:
    with pytest.raises(ValidationError, match="BirdA scope"):
        BirdARunRequest(
            run_scope=RunScope(
                run_id=UUID("00000000-0000-0000-0000-000000000001"),
                track="retail",
                mode="retail",
                subject_id="retail-demo",
                experiment_id="day3",
                config_hash="a" * 64,
            ),
            attempt_id=ATTEMPT_ID,
            current_input=user_datum("question"),
        )


def test_bird_c_request_rejects_bird_a_scope() -> None:
    with pytest.raises(ValidationError, match="BirdC scope"):
        BirdCRequest(
            run_scope=bird_scope(mode="a"),
            attempt_id=ATTEMPT_ID,
            current_phase=user_datum("phase"),
        )


def test_stop_outcome_has_closed_shape() -> None:
    stop = StopOutcome(
        kind="no_progress",
        reason_code="same_evidence_repeated",
        evidence_digest="a" * 64,
        retryable=False,
    )
    assert stop.kind is StopKind.NO_PROGRESS
    with pytest.raises(ValidationError):
        StopOutcome(kind="bird_coin_zero", reason_code="x", retryable=False)


def test_retail_outcome_requires_exactly_one_terminal_value() -> None:
    evidence = (
        InvestigationEvidence(
            evidence_id="query:baseline",
            kind="query",
            summary="Baseline result.",
            digest="a" * 64,
        ),
        InvestigationEvidence(
            evidence_id="reconciliation:baseline",
            kind="reconciliation",
            summary="Baseline reconciled.",
            digest="b" * 64,
        ),
    )
    report = InvestigationReport(
        conversation_id="retail-conversation-1",
        report_id=UUID(int=11),
        claims=(
            InvestigationClaim(
                claim_id="baseline_result",
                text="The baseline was measured.",
                evidence_refs=("query:baseline",),
            ),
        ),
        available_evidence=evidence,
        reconciliation_refs=("reconciliation:baseline",),
    )
    values = {
        "status": "completed",
        "terminal": report,
        "stop": None,
        "model_calls": 1,
        "tool_calls": 0,
        "prompt_policy_hash": "a" * 64,
        "rendered_prompt_hash": "b" * 64,
        "tool_hash": "c" * 64,
        "context_hash": "d" * 64,
        "config_hash": "e" * 64,
        "attempt_id": ATTEMPT_ID,
    }
    assert RetailRunOutcome(**values).status == "completed"
    with pytest.raises(ValidationError, match="forbids stop"):
        RetailRunOutcome.model_validate(values | {"stop": StopOutcome(
            kind="unsafe",
            reason_code="unsafe_output",
            retryable=False,
        )})


def test_retail_terminal_status_matches_typed_value() -> None:
    clarification = ClarificationRequest(
        conversation_id="retail-conversation-1",
        request_id=UUID(int=10),
        items=(
            ClarificationItem(
                slot="gmv_metric_definition",
                question="Which approved GMV definition should be used?",
                allowed_values=("item_amount", "payment_amount"),
            ),
        ),
    )
    values = {
        "terminal": clarification,
        "stop": None,
        "model_calls": 1,
        "tool_calls": 1,
        "prompt_policy_hash": "a" * 64,
        "rendered_prompt_hash": "b" * 64,
        "tool_hash": "c" * 64,
        "context_hash": "d" * 64,
        "config_hash": "e" * 64,
        "attempt_id": ATTEMPT_ID,
    }

    assert RetailRunOutcome(status="needs_input", **values).terminal.type == "clarification_request"
    with pytest.raises(ValidationError, match="completed status"):
        RetailRunOutcome(status="completed", **values)


def test_plan_rejects_missing_required_gmv_slot() -> None:
    facts = {
        "valid_order_statuses": ("delivered",),
        "time_field": "order_purchase_timestamp",
        "time_range": InvestigationTimeRange(
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            ended_at=datetime(2026, 4, 1, tzinfo=UTC),
        ),
        "analysis_grain": "month",
        "minimum_order_count": 10,
        "anomaly_threshold": Decimal("0.20"),
    }
    with pytest.raises(ValidationError, match="gmv_metric_definition"):
        ConfirmedInvestigationFacts.model_validate(facts)


def test_report_cannot_complete_with_unreferenced_claim() -> None:
    with pytest.raises(ValidationError, match="evidence"):
        InvestigationReport(
            conversation_id="retail-conversation-1",
            report_id=UUID(int=12),
            claims=(
                InvestigationClaim(
                    claim_id="missing",
                    text="GMV changed.",
                    evidence_refs=("query:missing",),
                ),
            ),
            available_evidence=(
                InvestigationEvidence(
                    evidence_id="query:baseline",
                    kind="query",
                    summary="Baseline.",
                    digest="a" * 64,
                ),
                InvestigationEvidence(
                    evidence_id="reconciliation:baseline",
                    kind="reconciliation",
                    summary="Reconciled.",
                    digest="b" * 64,
                ),
            ),
            reconciliation_refs=("reconciliation:baseline",),
        )


def test_plan_has_one_baseline_and_one_drilldown() -> None:
    facts = ConfirmedInvestigationFacts(
        gmv_metric_definition="item_amount",
        valid_order_statuses=("delivered",),
        time_field="order_purchase_timestamp",
        time_range=InvestigationTimeRange(
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            ended_at=datetime(2026, 4, 1, tzinfo=UTC),
        ),
        analysis_grain="month",
        minimum_order_count=10,
        anomaly_threshold=Decimal("0.20"),
    )
    common = {
        "question": "Measure GMV.",
        "measures": ("item_amount",),
        "grains": ("month",),
        "evidence_refs": ("knowledge:gmv",),
        "reconciliation_rule_refs": ("reconcile:total",),
    }
    plan = InvestigationPlan(
        conversation_id="retail-conversation-1",
        plan_id=UUID(int=13),
        confirmed_facts=facts,
        steps=(
            QueryPlanStep(step_id="baseline", kind="baseline", **common),
            QueryPlanStep(step_id="by_state", kind="drilldown", **common),
        ),
        evidence_refs=("knowledge:gmv",),
        reconciliation_rule_refs=("reconcile:total",),
        stop_conditions=("insufficient_data",),
    )

    assert [step.kind for step in plan.steps] == ["baseline", "drilldown"]


def test_retail_request_accepts_only_the_safe_context_shape() -> None:
    datum = user_datum("question")
    request = RetailRunRequest(
        run_scope=RunScope(
            run_id=UUID("00000000-0000-0000-0000-000000000001"),
            track="retail",
            mode="retail",
            subject_id="retail-demo",
            experiment_id="day3",
            config_hash="a" * 64,
        ),
        attempt_id=ATTEMPT_ID,
        current_input=datum,
        confirmed_facts=(datum,),
        evidence=(datum,),
        latest_error=None,
    )
    assert request.confirmed_facts == (datum,)


def test_bird_a_request_enforces_loop_budgets() -> None:
    values = {
        "run_scope": bird_scope(mode="a"),
        "attempt_id": ATTEMPT_ID,
        "current_input": user_datum("question"),
        "confirmed_facts": (),
        "evidence": (),
        "latest_error": None,
        "max_model_calls": 6,
        "max_tool_calls": 8,
    }
    assert BirdARunRequest(**values).max_tool_calls == 8
    with pytest.raises(ValidationError):
        BirdARunRequest.model_validate(values | {"max_model_calls": 7})


def test_bird_a_outcome_matches_its_terminal_status() -> None:
    values = {
        "status": "stopped",
        "final_output": None,
        "stop": StopOutcome(
            kind="budget_exhausted",
            reason_code="model_budget_exhausted",
            retryable=False,
        ),
        "model_calls": 6,
        "tool_calls": 1,
        "prompt_policy_hash": "a" * 64,
        "rendered_prompt_hash": "b" * 64,
        "tool_hash": "c" * 64,
        "context_hash": "d" * 64,
        "config_hash": "e" * 64,
        "attempt_id": ATTEMPT_ID,
    }
    assert BirdARunOutcome(**values).status == "stopped"
    with pytest.raises(ValidationError, match="completed outcomes"):
        BirdARunOutcome.model_validate(values | {"status": "completed"})


def test_bird_c_request_accepts_only_the_safe_context_shape() -> None:
    datum = user_datum("phase")
    request = BirdCRequest(
        run_scope=bird_scope(mode="c"),
        attempt_id=ATTEMPT_ID,
        current_phase=datum,
        confirmed_facts=(datum,),
        evidence=(datum,),
        latest_error=None,
    )
    assert request.evidence == (datum,)


def test_bird_c_candidates_are_closed_and_bounded() -> None:
    assert AskUserCandidate(type="ask_user", question="Which date field?").question
    assert SubmitSqlCandidate(type="submit_sql", sql="SELECT 1").sql == "SELECT 1"
    with pytest.raises(ValidationError):
        AskUserCandidate(type="ask_user", question="")


def test_bird_c_response_has_one_candidate_and_no_loop_state() -> None:
    response = BirdCResponse(
        candidate={"type": "submit_sql", "sql": "SELECT 1"},
        usage=UsageUnavailable(status="unavailable", reason_code="provider_usage_missing"),
        cost=CostUnavailable(status="unavailable", reason_code="usage_unavailable"),
        prompt_policy_hash="a" * 64,
        rendered_prompt_hash="b" * 64,
        tool_hash="c" * 64,
        context_hash="d" * 64,
        config_hash="e" * 64,
        attempt_id=ATTEMPT_ID,
    )
    assert isinstance(response.candidate, SubmitSqlCandidate)
    assert {"model_calls", "tool_calls", "checkpoint"}.isdisjoint(BirdCResponse.model_fields)
