"""Internal checkpoint identity and compatibility helpers."""

import json
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from hashlib import sha256
from typing import TypedDict
from uuid import UUID

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.conninfo import conninfo_to_dict
from pydantic import SecretStr

from commerce_agent.model.contracts import RunScope

RETAIL_STATE_SCHEMA_REVISION = "retail-state-v2"
RETAIL_NODE_REVISION = "retail-nodes-v2"


class RetailGraphStateV2(TypedDict):
    """Checkpoint-safe Retail state containing JSON-like values only."""

    state_schema_revision: str
    node_revision: str
    run_scope_json: str
    attempt_id: str
    current_node: str
    clarification_count: int
    replan_count: int
    repair_count: int
    model_call_count: int
    tool_call_count: int
    confirmed_facts_json: str
    investigation_plan_json: str | None
    pending_plan_step_ids: list[str]
    sql_candidate_json: str | None
    query_evidence_json: str
    reconciliation_json: str | None
    proposal_refs_json: str
    terminal_json: str | None
    pending_tool_batch_json: str | None
    provider_history_json: str
    revisions_json: str
    latest_error_json: str | None
    stop_reason_json: str | None
    prompt_policy_hash: str
    rendered_prompt_hash: str
    tool_hash: str
    context_hash: str
    config_hash: str
    history_json: str
    sql_candidate: str | None
    evidence_summaries: list[dict[str, str | int | bool | None]]
    final_output_json: str | None
    cleanup_complete: bool


RetailGraphState = RetailGraphStateV2


def canonical_checkpoint_json(state: Mapping[str, object]) -> str:
    forbidden = {"grant", "nonce", "signature", "hmac", "dsn", "seller_id", "reasoning_content"}

    def validate(value: object) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).casefold() in forbidden:
                    raise CheckpointIncompatible("sensitive_checkpoint_field")
                validate(item)
        elif isinstance(value, list | tuple):
            for item in value:
                validate(item)

    validate(state)
    return json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class CheckpointIncompatible(RuntimeError):
    """A persisted state cannot be safely restored by this graph revision."""

    def __init__(self, reason_code: str) -> None:
        super().__init__("checkpoint state is incompatible")
        self.reason_code = reason_code


class CheckpointInfrastructureError(RuntimeError):
    """Checkpoint infrastructure is unavailable or configured unsafely."""

    def __init__(self, reason_code: str) -> None:
        super().__init__("checkpoint infrastructure is unavailable")
        self.reason_code = reason_code


def derive_thread_id(run_scope: RunScope, attempt_id: UUID) -> str:
    """Derive a bounded opaque LangGraph thread ID from canonical run identity."""

    identity = {
        "attempt_id": str(attempt_id),
        "run_scope": run_scope.model_dump(mode="json"),
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def assert_checkpoint_compatible(
    state: Mapping[str, object],
    *,
    expected_config_hash: str,
    expected_run_scope: RunScope | None = None,
    expected_attempt_id: UUID | None = None,
    expected_state_schema_revision: str = RETAIL_STATE_SCHEMA_REVISION,
    expected_node_revision: str = RETAIL_NODE_REVISION,
) -> None:
    """Reject persisted state whose identity or compatibility binding changed."""

    checks = (
        ("state_schema_revision", expected_state_schema_revision, "state_schema_mismatch"),
        ("node_revision", expected_node_revision, "node_revision_mismatch"),
        ("config_hash", expected_config_hash, "config_hash_mismatch"),
    )
    for field_name, expected, reason_code in checks:
        if state.get(field_name) != expected:
            raise CheckpointIncompatible(reason_code)
    if (
        expected_run_scope is not None
        and state.get("run_scope_json") != expected_run_scope.model_dump_json()
    ):
        raise CheckpointIncompatible("run_scope_mismatch")
    if expected_attempt_id is not None and state.get("attempt_id") != str(
        expected_attempt_id
    ):
        raise CheckpointIncompatible("attempt_id_mismatch")


def create_memory_saver() -> object:
    """Create a memory saver only with strict, non-pickle deserialization."""

    if os.environ.get("LANGGRAPH_STRICT_MSGPACK") != "true":
        raise CheckpointInfrastructureError("strict_msgpack_required")

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    serializer = JsonPlusSerializer(
        pickle_fallback=False,
        allowed_json_modules=None,
        allowed_msgpack_modules=None,
    )
    return InMemorySaver(serde=serializer)


@asynccontextmanager
async def open_postgres_saver(dsn: SecretStr) -> AsyncIterator[object]:
    """Open the reviewed runtime saver only for the checkpoint writer identity."""

    if os.environ.get("LANGGRAPH_STRICT_MSGPACK") != "true":
        raise CheckpointInfrastructureError("strict_msgpack_required")
    try:
        parameters = conninfo_to_dict(dsn.get_secret_value())
    except Exception as error:
        raise CheckpointInfrastructureError("checkpoint_identity_invalid") from error
    if parameters.get("user") != "checkpoint_writer":
        raise CheckpointInfrastructureError("checkpoint_identity_invalid")
    required_parameters = {
        "host": "127.0.0.1",
        "port": "5432",
        "dbname": "commerce_analyst",
        "application_name": "commerce_retail_checkpoint",
        "options": "-csearch_path=checkpoint,pg_catalog",
    }
    if any(parameters.get(key) != value for key, value in required_parameters.items()):
        raise CheckpointInfrastructureError("checkpoint_dsn_invalid")
    serializer = JsonPlusSerializer(
        pickle_fallback=False,
        allowed_json_modules=None,
        allowed_msgpack_modules=None,
    )
    entered = False
    try:
        async with AsyncPostgresSaver.from_conn_string(
            dsn.get_secret_value(),
            serde=serializer,
        ) as saver:
            entered = True
            yield saver
    except CheckpointInfrastructureError:
        raise
    except Exception as error:
        if not entered:
            raise CheckpointInfrastructureError("checkpoint_connection_failed") from error
        raise
