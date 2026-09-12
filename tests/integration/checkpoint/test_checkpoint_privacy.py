import json
import os
from datetime import timedelta
from hashlib import sha256
from uuid import UUID

import psycopg
import pytest
from pydantic import SecretStr

from commerce_agent.context_builder.builder import scope_digest
from commerce_agent.context_builder.profiles import ProfileRegistry
from commerce_agent.model._postgres_turn_store import PostgresProviderTurnStore
from commerce_agent.model._turn_store import AttemptRef, PrivateProviderTurn
from commerce_agent.model.contracts import FinalOutput
from commerce_agent.model.fake import FakeModel
from commerce_agent.orchestration._checkpoint import derive_thread_id, open_postgres_saver
from tests.integration.checkpoint.test_retail_postgres_resume import (
    CONFIG_ROOT,
    NOW,
    make_graph,
    make_request,
    make_response,
)

pytestmark = pytest.mark.postgres

PRIVATE_CONTENT = "PRIVATE_PROVIDER_CONTENT_SENTINEL_TASK11"
PRIVATE_REASONING = "PRIVATE_REASONING_CONTENT_SENTINEL_TASK11"
FORBIDDEN_CHECKPOINT_SENTINELS = (
    PRIVATE_CONTENT,
    PRIVATE_REASONING,
    "SYNTHETIC_API_KEY_SENTINEL_TASK11",
    "SYNTHETIC_DSN_SENTINEL_TASK11",
    "CALLABLE_MODULE_SENTINEL_TASK11",
    "BIRD_STATE_SENTINEL_TASK11",
)


class FixedClock:
    def now(self):
        return NOW


def private_turn(*, scope: str, attempt_id: UUID) -> PrivateProviderTurn:
    payload = {
        "content": PRIVATE_CONTENT,
        "reasoning_content": PRIVATE_REASONING,
        "tool_calls": [],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return PrivateProviderTurn(
        scope_digest=scope,
        attempt_id=attempt_id,
        sequence=0,
        provider="deepseek",
        model="deepseek-v4-flash",
        content=PRIVATE_CONTENT,
        reasoning_content=PRIVATE_REASONING,
        tool_calls_json="[]",
        payload_sha256=sha256(canonical.encode("utf-8")).hexdigest(),
        expected_tool_call_ids=(),
        token_weight=12,
        created_at=NOW,
        last_used_at=NOW,
        absolute_expires_at=NOW + timedelta(days=7),
    )


async def checkpoint_contains(connection: psycopg.AsyncConnection, needle: str) -> bool:
    cursor = await connection.execute(
        "SELECT "
        "EXISTS (SELECT 1 FROM checkpoint.checkpoints "
        "WHERE checkpoint::text LIKE '%%' || %s || '%%' "
        "OR metadata::text LIKE '%%' || %s || '%%') "
        "OR EXISTS (SELECT 1 FROM checkpoint.checkpoint_blobs "
        "WHERE blob IS NOT NULL AND position(convert_to(%s, 'UTF8') in blob) > 0) "
        "OR EXISTS (SELECT 1 FROM checkpoint.checkpoint_writes "
        "WHERE position(convert_to(%s, 'UTF8') in blob) > 0)",
        (needle, needle, needle, needle),
    )
    row = await cursor.fetchone()
    assert row is not None
    return bool(row[0])


@pytest.mark.asyncio
async def test_private_provider_payload_never_enters_checkpoint_and_is_cleared() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    request = make_request(
        registry,
        attempt_id=UUID(int=20_000),
        subject_id="checkpoint-privacy",
    )
    thread_id = derive_thread_id(request.run_scope, request.attempt_id)
    attempt = AttemptRef(
        scope_digest=scope_digest(request.run_scope),
        attempt_id=request.attempt_id,
    )
    turn_store = PostgresProviderTurnStore(
        SecretStr(os.environ["PRODUCT_MODEL_STATE_DATABASE_DSN"]),
        clock=FixedClock(),
    )
    reference = await turn_store.save(
        private_turn(scope=attempt.scope_digest, attempt_id=request.attempt_id)
    )
    response = make_response(
        request,
        sequence=0,
        output=FinalOutput(type="final", content="Public final answer."),
        finish_reason="stop",
    ).model_copy(update={"provider_turn_ref": reference})
    gateway = FakeModel([response])

    async with open_postgres_saver(
        SecretStr(os.environ["PRODUCT_CHECKPOINT_DATABASE_DSN"])
    ) as saver:
        try:
            interrupted = await make_graph(
                registry,
                gateway,
                saver,
                interrupt_after=("route_output",),
                turn_store=turn_store,
            ).run(request)
            assert interrupted.status == "stopped"

            admin = await psycopg.AsyncConnection.connect(
                os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
            )
            try:
                for sentinel in FORBIDDEN_CHECKPOINT_SENTINELS:
                    assert await checkpoint_contains(admin, sentinel) is False
                cursor = await admin.execute(
                    "SELECT payload ->> 'content', payload ->> 'reasoning_content' "
                    "FROM model_state.provider_turn "
                    "WHERE scope_digest = %s AND attempt_id = %s",
                    (attempt.scope_digest, attempt.attempt_id),
                )
                assert await cursor.fetchone() == (PRIVATE_CONTENT, PRIVATE_REASONING)
            finally:
                await admin.close()

            resumed = await make_graph(
                registry,
                gateway,
                saver,
                turn_store=turn_store,
            ).run(request)
            assert resumed.status == "stopped"
            assert resumed.stop is not None
            assert resumed.stop.reason_code == "typed_retail_terminal_required"

            admin = await psycopg.AsyncConnection.connect(
                os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
            )
            try:
                cursor = await admin.execute(
                    "SELECT payload, terminal_at IS NOT NULL "
                    "FROM model_state.provider_turn "
                    "WHERE scope_digest = %s AND attempt_id = %s",
                    (attempt.scope_digest, attempt.attempt_id),
                )
                assert await cursor.fetchone() == (None, True)
                for sentinel in FORBIDDEN_CHECKPOINT_SENTINELS:
                    assert await checkpoint_contains(admin, sentinel) is False
            finally:
                await admin.close()
        finally:
            await saver.adelete_thread(thread_id)
            await turn_store.delete_attempt(attempt)
            admin = await psycopg.AsyncConnection.connect(
                os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
            )
            try:
                await admin.execute(
                    "DELETE FROM model_state.provider_turn "
                    "WHERE scope_digest = %s AND attempt_id = %s",
                    (attempt.scope_digest, attempt.attempt_id),
                )
                await admin.commit()
            finally:
                await admin.close()
