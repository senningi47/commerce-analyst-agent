"""Contract tests for the frozen BIRD-Interact official revision contract.

The fixture is generated mechanically from the pinned upstream checkout by
`scripts/freeze_bird_official_contract.py`; these tests pin the loader's
public surface and the security-relevant invariants (action/cost table,
SubmitSQLResponse whitelist, no GT field names).
"""

from pathlib import Path

from commerce_agent.evaluation import BIRD_SOURCE_REVISION, load_official_contract

EXPECTED_ACTIONS = {
    "execute_sql": "1",
    "get_schema": "1",
    "get_all_column_meanings": "1",
    "get_column_meaning": "0.5",
    "get_all_external_knowledge_names": "0.5",
    "get_knowledge_definition": "0.5",
    "get_all_knowledge_definitions": "1",
    "ask_user": "2",
    "submit_sql": "3",
}
FORBIDDEN_FIELD_TOKENS = ("sol_sql", "test_cases", "gold", "evaluator", "gt_")


def test_source_revision_is_frozen() -> None:
    assert BIRD_SOURCE_REVISION == "451fe2c3518ee1cf908d8139e2913483bd519381"


def test_load_official_contract_matches_frozen_table() -> None:
    contract = load_official_contract()
    assert contract.source_revision == BIRD_SOURCE_REVISION
    assert {a.name: str(a.coin_cost) for a in contract.actions} == EXPECTED_ACTIONS
    assert contract.submit_sql_response_fields == frozenset(
        {"passed", "message", "reward", "phase_completed", "has_follow_up", "follow_up_query"}
    )
    assert {"task_id", "mode", "message"} <= contract.run_session_request_fields
    assert {"task_id", "mode", "session_id", "response", "state", "adk_available"} <= (
        contract.run_session_response_fields
    )


def test_contract_leaks_no_gt_field_names() -> None:
    contract = load_official_contract()
    all_names = (
        contract.init_session_request_fields
        | contract.init_session_response_fields
        | contract.run_session_request_fields
        | contract.run_session_response_fields
        | contract.submit_sql_response_fields
        | contract.orchestrator_cli.json_output_fields
    )
    lowered = {name.lower() for name in all_names}
    for token in FORBIDDEN_FIELD_TOKENS:
        assert not any(token in name for name in lowered), token


def test_fixture_file_is_committed_path() -> None:
    from commerce_agent.evaluation.contracts import _FIXTURE_PATH

    assert Path(_FIXTURE_PATH) == Path("tests/fixtures/bird/official-contract.v1.json")
