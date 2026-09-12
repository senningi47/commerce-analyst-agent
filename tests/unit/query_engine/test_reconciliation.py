from decimal import Decimal

import pytest

from commerce_agent.query_engine._ast_policy import AstPolicy
from commerce_agent.query_engine.contracts import (
    DecimalToleranceRule,
    EvidenceResult,
    GrainRule,
    NullRule,
    ReconciliationRequest,
    SetRule,
    TotalRule,
)
from commerce_agent.query_engine.engine import QueryEngine
from tests.unit.query_engine.test_engine import RecordingExecutor


def evidence_result(ref: str, rows: list[dict[str, object]]) -> EvidenceResult:
    return EvidenceResult(
        evidence_id=ref,
        columns=tuple(rows[0]) if rows else (),
        rows=tuple(rows),
        query_fingerprint="a" * 64,
    )


def in_memory_query_engine() -> QueryEngine:
    return QueryEngine(policy=AstPolicy(), executor=RecordingExecutor())


def test_reconcile_requires_total_and_decimal_rules_to_pass() -> None:
    engine = in_memory_query_engine()
    result = engine.reconcile(
        ReconciliationRequest(
            evidence=(
                evidence_result("baseline", [{"amount": "100.00"}]),
                evidence_result("parts", [{"amount": "60.00"}, {"amount": "40.00"}]),
            ),
            rules=(
                TotalRule(
                    type="total",
                    parent_ref="baseline",
                    child_ref="parts",
                    column="amount",
                ),
                DecimalToleranceRule(
                    type="decimal_tolerance",
                    left_ref="baseline",
                    right_ref="parts",
                    column="amount",
                    absolute_tolerance=Decimal("0.01"),
                ),
            ),
        )
    )

    assert result.passed is True
    assert all(check.passed for check in result.checks)


@pytest.mark.parametrize(
    ("evidence", "rule", "reason_code"),
    [
        (
            (evidence_result("rows", [{"state": "SP"}, {"state": "SP"}]),),
            GrainRule(type="grain", evidence_ref="rows", key_columns=("state",)),
            "grain_not_unique",
        ),
        (
            (
                evidence_result("left", [{"state": "SP"}]),
                evidence_result("right", [{"state": "RJ"}]),
            ),
            SetRule(
                type="set",
                left_ref="left",
                right_ref="right",
                columns=("state",),
            ),
            "set_mismatch",
        ),
        (
            (evidence_result("rows", [{"amount": None}]),),
            NullRule(type="null", evidence_ref="rows", columns=("amount",)),
            "null_policy_failed",
        ),
    ],
)
def test_reconciliation_detects_invalid_evidence(evidence, rule, reason_code: str) -> None:
    result = in_memory_query_engine().reconcile(
        ReconciliationRequest(evidence=evidence, rules=(rule,))
    )

    assert result.passed is False
    assert reason_code in {check.reason_code for check in result.checks}
