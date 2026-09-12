import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import cast
from uuid import UUID

import pytest
from pydantic import SecretStr

from commerce_agent.model.contracts import RunScope
from commerce_agent.orchestration._checkpoint import (
    CheckpointIncompatible,
    CheckpointInfrastructureError,
    RetailGraphState,
    assert_checkpoint_compatible,
    create_memory_saver,
    derive_thread_id,
    open_postgres_saver,
)

ATTEMPT_ID = UUID("00000000-0000-0000-0000-000000000002")
OTHER_ATTEMPT_ID = UUID("00000000-0000-0000-0000-000000000003")


def retail_scope(*, subject_id: str = "retail-demo") -> RunScope:
    return RunScope(
        run_id=UUID("00000000-0000-0000-0000-000000000001"),
        track="retail",
        mode="retail",
        subject_id=subject_id,
        experiment_id="day3",
        config_hash="a" * 64,
    )


def retail_state_dict(**updates: object) -> dict[str, object]:
    state: dict[str, object] = {
        "state_schema_revision": "retail-state-v2",
        "node_revision": "retail-nodes-v2",
        "run_scope_json": retail_scope().model_dump_json(),
        "attempt_id": str(ATTEMPT_ID),
        "current_node": "prepare_context",
        "clarification_count": 0,
        "replan_count": 0,
        "repair_count": 0,
        "model_call_count": 0,
        "tool_call_count": 0,
        "prompt_policy_hash": "a" * 64,
        "rendered_prompt_hash": "b" * 64,
        "tool_hash": "c" * 64,
        "context_hash": "d" * 64,
        "config_hash": "a" * 64,
        "history_json": "[]",
        "pending_tool_batch_json": None,
        "sql_candidate": None,
        "latest_error_json": None,
        "evidence_summaries": [],
        "final_output_json": None,
        "stop_reason_json": None,
    }
    state.update(updates)
    json.dumps(state, allow_nan=False)
    return state


def test_reviewed_langgraph_public_imports_are_available() -> None:
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.graph import END, START, StateGraph

    assert InMemorySaver and AsyncPostgresSaver and StateGraph and START and END


def test_reviewed_dependency_versions_are_stable_and_psycopg_compatible() -> None:
    assert version("langgraph") == "1.2.11"
    assert version("langgraph-checkpoint-postgres") == "3.1.2"
    assert version("psycopg").startswith("3.3.")


def test_thread_id_is_deterministic_private_and_under_255_chars() -> None:
    first = derive_thread_id(retail_scope(subject_id="customer-visible-name"), ATTEMPT_ID)
    second = derive_thread_id(retail_scope(subject_id="customer-visible-name"), ATTEMPT_ID)
    assert first == second
    assert len(first) == 64
    assert "customer-visible-name" not in first
    assert derive_thread_id(retail_scope(), OTHER_ATTEMPT_ID) != first


def test_checkpoint_rejects_state_node_and_config_revision_changes() -> None:
    state = retail_state_dict(
        state_schema_revision="retail-state-v2",
        node_revision="retail-nodes-v2",
        config_hash="a" * 64,
    )
    with pytest.raises(CheckpointIncompatible) as caught:
        assert_checkpoint_compatible(state, expected_config_hash="b" * 64)
    assert caught.value.reason_code == "config_hash_mismatch"


@pytest.mark.parametrize(
    ("state_updates", "expected", "reason_code"),
    [
        (
            {"state_schema_revision": "retail-state-v1"},
            {},
            "state_schema_mismatch",
        ),
        ({"node_revision": "retail-nodes-v1"}, {}, "node_revision_mismatch"),
        (
            {},
            {"expected_run_scope": retail_scope(subject_id="other-subject")},
            "run_scope_mismatch",
        ),
        ({}, {"expected_attempt_id": OTHER_ATTEMPT_ID}, "attempt_id_mismatch"),
    ],
)
def test_checkpoint_rejects_each_incompatible_identity(
    state_updates: dict[str, object],
    expected: dict[str, object],
    reason_code: str,
) -> None:
    with pytest.raises(CheckpointIncompatible) as caught:
        assert_checkpoint_compatible(
            retail_state_dict(**state_updates),
            expected_config_hash="a" * 64,
            **expected,
        )
    assert caught.value.reason_code == reason_code


def test_retail_graph_state_is_json_like() -> None:
    state = cast(RetailGraphState, retail_state_dict())
    assert json.loads(json.dumps(state, allow_nan=False))["current_node"] == "prepare_context"


def test_memory_saver_factory_requires_strict_msgpack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGGRAPH_STRICT_MSGPACK", raising=False)
    with pytest.raises(CheckpointInfrastructureError) as caught:
        create_memory_saver()
    assert caught.value.reason_code == "strict_msgpack_required"

    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    assert create_memory_saver().__class__.__name__ == "InMemorySaver"


@pytest.mark.parametrize(
    "role",
    ["commerce_admin", "model_state_writer", "agent_reader", "knowledge_reader"],
)
@pytest.mark.asyncio
async def test_runtime_saver_accepts_only_checkpoint_writer(
    role: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    dsn = SecretStr(
        f"postgresql://{role}:secret@127.0.0.1:5432/commerce_analyst"
        "?application_name=commerce_retail_checkpoint"
        "&options=-csearch_path%3Dcheckpoint%2Cpg_catalog"
    )

    with pytest.raises(CheckpointInfrastructureError) as caught:
        async with open_postgres_saver(dsn):
            pass

    assert caught.value.reason_code == "checkpoint_identity_invalid"


@pytest.mark.parametrize(
    "dsn_value",
    [
        (
            "postgresql://checkpoint_writer:secret@localhost:5432/commerce_analyst"
            "?application_name=commerce_retail_checkpoint"
            "&options=-csearch_path%3Dcheckpoint%2Cpg_catalog"
        ),
        (
            "postgresql://checkpoint_writer:secret@127.0.0.1:5433/commerce_analyst"
            "?application_name=commerce_retail_checkpoint"
            "&options=-csearch_path%3Dcheckpoint%2Cpg_catalog"
        ),
        (
            "postgresql://checkpoint_writer:secret@127.0.0.1:5432/other_database"
            "?application_name=commerce_retail_checkpoint"
            "&options=-csearch_path%3Dcheckpoint%2Cpg_catalog"
        ),
        (
            "postgresql://checkpoint_writer:secret@127.0.0.1:5432/commerce_analyst"
            "?application_name=other_application"
            "&options=-csearch_path%3Dcheckpoint%2Cpg_catalog"
        ),
        (
            "postgresql://checkpoint_writer:secret@127.0.0.1:5432/commerce_analyst"
            "?application_name=commerce_retail_checkpoint"
            "&options=-csearch_path%3Dmodel_state%2Cpg_catalog"
        ),
    ],
)
@pytest.mark.asyncio
async def test_runtime_saver_rejects_noncanonical_product_endpoint(
    dsn_value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")

    with pytest.raises(CheckpointInfrastructureError) as caught:
        async with open_postgres_saver(SecretStr(dsn_value)):
            pass

    assert caught.value.reason_code == "checkpoint_dsn_invalid"


@pytest.mark.asyncio
async def test_runtime_saver_opens_with_strict_serializer_and_never_runs_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    dsn_value = (
        "postgresql://checkpoint_writer:secret@127.0.0.1:5432/commerce_analyst"
        "?application_name=commerce_retail_checkpoint"
        "&options=-csearch_path%3Dcheckpoint%2Cpg_catalog"
    )
    observed: dict[str, object] = {}

    class FakeSaver:
        async def setup(self) -> None:
            raise AssertionError("runtime saver must not call setup")

    saver = FakeSaver()

    @asynccontextmanager
    async def fake_from_conn_string(
        conn_string: str,
        *,
        serde: object,
    ) -> AsyncIterator[FakeSaver]:
        observed["conn_string"] = conn_string
        observed["serde"] = serde
        yield saver

    class FakeAsyncPostgresSaver:
        from_conn_string = staticmethod(fake_from_conn_string)

    monkeypatch.setattr(
        "commerce_agent.orchestration._checkpoint.AsyncPostgresSaver",
        FakeAsyncPostgresSaver,
    )

    async with open_postgres_saver(SecretStr(dsn_value)) as opened:
        assert opened is saver

    assert observed["conn_string"] == dsn_value
    serializer = observed["serde"]
    assert serializer.__class__.__name__ == "JsonPlusSerializer"
    assert serializer.pickle_fallback is False
