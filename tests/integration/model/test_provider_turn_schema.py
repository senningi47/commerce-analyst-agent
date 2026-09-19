import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.postgres
NOW = datetime(2026, 9, 6, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_runtime_state_schemas_table_owners_and_constraints_are_fixed() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT n.nspname, pg_get_userbyid(n.nspowner) "
            "FROM pg_namespace AS n WHERE n.nspname IN ('checkpoint', 'model_state') "
            "ORDER BY n.nspname"
        )
        assert await cursor.fetchall() == [
            ("checkpoint", "checkpoint_owner"),
            ("model_state", "model_state_owner"),
        ]
        cursor = await connection.execute(
            "SELECT pg_get_userbyid(c.relowner) FROM pg_class AS c "
            "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'model_state' AND c.relname = 'provider_turn'"
        )
        assert await cursor.fetchone() == ("model_state_owner",)
        cursor = await connection.execute(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'model_state.provider_turn'::regclass ORDER BY conname"
        )
        names = {row[0] for row in await cursor.fetchall()}
        assert {
            "ck_provider_turn_absolute_expiry",
            "ck_provider_turn_last_used_at",
            "ck_provider_turn_payload_sha256",
            "ck_provider_turn_scope_digest",
            "ck_provider_turn_sequence",
            "ck_provider_turn_terminal_payload",
            "ck_provider_turn_token_weight",
            "pk_provider_turn",
            "uq_provider_turn_scope_attempt_sequence",
        } <= names
    finally:
        await connection.close()


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("scope_digest", "invalid"),
        ("payload_sha256", "invalid"),
        ("sequence", -1),
        ("token_weight", -1),
        ("last_used_at", NOW - timedelta(seconds=1)),
        ("absolute_expires_at", NOW + timedelta(days=6)),
        ("terminal_at", NOW + timedelta(hours=1)),
        ("payload", None),
    ],
)
@pytest.mark.asyncio
async def test_provider_turn_row_invariants_fail_closed(
    field: str,
    invalid_value: object,
) -> None:
    values: dict[str, object] = {
        "turn_id": uuid4(),
        "scope_digest": "a" * 64,
        "attempt_id": uuid4(),
        "sequence": 0,
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "payload": Jsonb({"content": None, "reasoning_content": None, "tool_calls": []}),
        "payload_sha256": "b" * 64,
        "expected_tool_call_ids": [],
        "token_weight": 0,
        "created_at": NOW,
        "last_used_at": NOW,
        "absolute_expires_at": NOW + timedelta(days=7),
        "terminal_at": None,
    }
    values[field] = invalid_value
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            await connection.execute(
                "INSERT INTO model_state.provider_turn "
                "(turn_id, scope_digest, attempt_id, sequence, provider, model, payload, "
                "payload_sha256, expected_tool_call_ids, token_weight, created_at, "
                "last_used_at, absolute_expires_at, terminal_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                tuple(values.values()),
            )
    finally:
        await connection.rollback()
        await connection.close()
