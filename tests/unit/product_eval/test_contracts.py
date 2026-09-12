from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from commerce_agent.product_eval.contracts import ProductScenario

NOW = datetime(2026, 9, 6, 4, 0, tzinfo=UTC)


def scenario_payload() -> dict[str, object]:
    return {
        "scenario_id": "seller-risk-investigation-v1",
        "fixture_revision": "day4-development-v1",
        "visibility": "development",
        "initial_question": "Investigate seller delivery risk.",
        "scripted_clarifications": [
            {"slot": "time_field", "response": "order_purchase_timestamp"}
        ],
        "analyst_actor_ref": "actor:analyst",
        "approver_actor_ref": "actor:approver",
        "actor_steps": [
            {
                "action": "approve_execute",
                "actor_ref": "actor:approver",
                "proposal_index": 0,
            }
        ],
        "clock_steps": [NOW.isoformat()],
        "ops_initial_state": {},
        "reset_manifest_revision": "scenario-reset-v1",
        "gold_tables": ["orders"],
        "gold_columns": ["order_id"],
        "gold_values": ["delivered"],
        "reference_query_assertions": [
            {
                "sql": "SELECT task_ref FROM ops_read.investigation_tasks",
                "expected_columns": ["task_ref"],
                "minimum_rows": 1,
            }
        ],
        "database_assertions": [],
        "required_clarification_slots": ["time_field"],
        "forbidden_clarification_slots": ["business_value"],
        "evidence_ids": ["query:baseline"],
        "operation_expectations": [
            {
                "command_type": "open_seller_risk_case",
                "terminal_status": "succeeded",
                "count": 1,
            }
        ],
        "readback_expectations": [
            {
                "sql": "SELECT task_ref FROM ops_read.investigation_tasks",
                "expected_columns": ["task_ref"],
                "minimum_rows": 1,
            }
        ],
        "audit_expectations": [{"event_type": "operation_executed", "count": 1}],
        "allowed_failure_script": [],
        "expected_terminal_status": "completed",
    }


def test_scenario_requires_versioned_fixture_and_deterministic_assertions() -> None:
    scenario = ProductScenario.model_validate(scenario_payload())

    assert scenario.fixture_revision == "day4-development-v1"
    assert scenario.required_clarification_slots
    assert scenario.operation_expectations
    assert scenario.audit_expectations


def test_development_fixture_rejects_final_closed_classification() -> None:
    with pytest.raises(ValidationError, match="visibility"):
        ProductScenario.model_validate(scenario_payload() | {"visibility": "final_closed"})


def test_scenario_is_immutable_and_rejects_unknown_fields() -> None:
    scenario = ProductScenario.model_validate(scenario_payload())

    with pytest.raises(ValidationError, match="frozen"):
        scenario.visibility = "regression"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ProductScenario.model_validate(scenario_payload() | {"hidden_answer": "private"})


def test_scenario_requires_exactly_one_assertion_source() -> None:
    payload = scenario_payload()
    payload["database_assertions"] = [
        {"name": "seed_state", "passed": True, "reason_code": "check_passed"}
    ]

    with pytest.raises(ValidationError, match="exactly one assertion source"):
        ProductScenario.model_validate(payload)


def test_scenario_rejects_naive_clock() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        ProductScenario.model_validate(
            scenario_payload()
            | {"clock_steps": [datetime(2026, 9, 6, 4, 0)]}  # noqa: DTZ001
        )


def test_scenario_rejects_unreviewed_failure_point() -> None:
    with pytest.raises(ValidationError, match="allowed_failure_script"):
        ProductScenario.model_validate(
            scenario_payload() | {"allowed_failure_script": ["arbitrary_failpoint"]}
        )
