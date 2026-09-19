"""PostgreSQL adapter for Retail provider-private assistant turns."""

import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
from pydantic import SecretStr, ValidationError

from commerce_agent.model._turn_store import (
    AttemptRef,
    Clock,
    PrivateProviderTurn,
    _canonical_json,
    _payload_sha256,
)
from commerce_agent.model.contracts import ProviderTurnRef
from commerce_agent.model.errors import ModelStateError, ModelTransportError

_SET_LOCAL_STATEMENTS = (
    "SET LOCAL statement_timeout = '5s'",
    "SET LOCAL lock_timeout = '1s'",
    "SET LOCAL idle_in_transaction_session_timeout = '10s'",
    "SET LOCAL search_path = model_state, pg_catalog",
)

_INSERT_TURN = (
    "INSERT INTO model_state.provider_turn "
    "(turn_id, scope_digest, attempt_id, sequence, provider, model, payload, "
    "payload_sha256, expected_tool_call_ids, token_weight, created_at, last_used_at, "
    "absolute_expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
_SELECT_TURN = (
    "SELECT turn_id, scope_digest, attempt_id, sequence, provider, model, payload, "
    "payload_sha256, expected_tool_call_ids, token_weight, created_at, last_used_at, "
    "absolute_expires_at, terminal_at FROM model_state.provider_turn "
    "WHERE turn_id = %s FOR UPDATE"
)
_RENEW_TURN = (
    "UPDATE model_state.provider_turn SET last_used_at = %s "
    "WHERE turn_id = %s AND scope_digest = %s AND attempt_id = %s "
    "AND terminal_at IS NULL AND payload IS NOT NULL"
)
_DELETE_ATTEMPT = (
    "UPDATE model_state.provider_turn SET terminal_at = %s, payload = NULL "
    "WHERE scope_digest = %s AND attempt_id = %s "
    "AND terminal_at IS NULL"
)
_IDLE_TTL = timedelta(hours=24)


async def _begin_guarded_transaction(connection: psycopg.AsyncConnection[Any]) -> None:
    await connection.execute("BEGIN")
    for statement in _SET_LOCAL_STATEMENTS:
        await connection.execute(statement)


async def _rollback_safely(connection: psycopg.AsyncConnection[Any]) -> None:
    try:
        await connection.rollback()
    except psycopg.Error:
        pass


class PostgresProviderTurnStore:
    """Persist provider-private turns behind scope-bound opaque references."""

    def __init__(self, dsn: SecretStr, *, clock: Clock) -> None:
        self._dsn = dsn
        self._clock = clock

    async def _connect(self) -> psycopg.AsyncConnection[Any]:
        try:
            return await psycopg.AsyncConnection.connect(
                self._dsn.get_secret_value(),
                application_name="commerce_model_state",
                connect_timeout=3,
            )
        except psycopg.Error as error:
            raise ModelTransportError(
                "state_connection_failed",
                "provider state connection failed",
                retryable=True,
            ) from error

    async def save(self, turn: PrivateProviderTurn) -> ProviderTurnRef:
        """Insert one validated private turn without upsert semantics."""

        if _payload_sha256(turn) != turn.payload_sha256:
            raise ModelStateError(
                "state_digest_mismatch",
                "provider turn payload digest does not match",
                retryable=False,
            )
        turn_id = uuid4()
        payload = Jsonb(
            {
                "content": turn.content,
                "reasoning_content": turn.reasoning_content,
                "tool_calls": json.loads(turn.tool_calls_json),
            }
        )
        connection = await self._connect()
        try:
            await _begin_guarded_transaction(connection)
            await connection.execute(
                _INSERT_TURN,
                (
                    turn_id,
                    turn.scope_digest,
                    turn.attempt_id,
                    turn.sequence,
                    turn.provider,
                    turn.model,
                    payload,
                    turn.payload_sha256,
                    list(turn.expected_tool_call_ids),
                    turn.token_weight,
                    turn.created_at,
                    turn.last_used_at,
                    turn.absolute_expires_at,
                ),
            )
            await connection.commit()
        except psycopg.Error as error:
            await _rollback_safely(connection)
            if error.sqlstate == "23505":
                raise ModelStateError(
                    "state_sequence_duplicate",
                    "provider turn sequence already exists for this attempt",
                    retryable=False,
                ) from error
            raise ModelTransportError(
                "state_write_failed",
                "provider state write failed",
                retryable=True,
            ) from error
        finally:
            await connection.close()
        return ProviderTurnRef(
            turn_id=turn_id,
            scope_digest=turn.scope_digest,
            attempt_id=turn.attempt_id,
            sequence=turn.sequence,
            payload_sha256=turn.payload_sha256,
            expected_tool_call_ids=turn.expected_tool_call_ids,
            token_weight=turn.token_weight,
            expires_at=turn.absolute_expires_at,
        )

    async def resolve(self, ref: ProviderTurnRef) -> PrivateProviderTurn:
        """Resolve one exact active reference and renew its idle timestamp."""

        connection = await self._connect()
        try:
            await _begin_guarded_transaction(connection)
            cursor = await connection.execute(_SELECT_TURN, (ref.turn_id,))
            row = await cursor.fetchone()
            if row is None or row[13] is not None or row[6] is None:
                raise ModelStateError(
                    "state_not_found",
                    "provider turn not found",
                    retryable=False,
                )
            payload = row[6]
            if not isinstance(payload, dict) or set(payload) != {
                "content",
                "reasoning_content",
                "tool_calls",
            }:
                raise ModelStateError(
                    "state_corrupt",
                    "provider turn state is invalid",
                    retryable=False,
                )
            turn = PrivateProviderTurn(
                scope_digest=row[1],
                attempt_id=row[2],
                sequence=row[3],
                provider=row[4],
                model=row[5],
                content=payload["content"],
                reasoning_content=payload["reasoning_content"],
                tool_calls_json=_canonical_json(payload["tool_calls"]),
                payload_sha256=row[7],
                expected_tool_call_ids=tuple(row[8]),
                token_weight=row[9],
                created_at=row[10],
                last_used_at=row[11],
                absolute_expires_at=row[12],
            )
            if _payload_sha256(turn) != turn.payload_sha256:
                raise ModelStateError(
                    "state_digest_mismatch",
                    "provider turn payload digest does not match",
                    retryable=False,
                )
            bindings = (
                ("turn", ref.turn_id, row[0]),
                ("scope", ref.scope_digest, turn.scope_digest),
                ("attempt", ref.attempt_id, turn.attempt_id),
                ("sequence", ref.sequence, turn.sequence),
                ("payload digest", ref.payload_sha256, turn.payload_sha256),
                ("expected tool calls", ref.expected_tool_call_ids, turn.expected_tool_call_ids),
                ("token weight", ref.token_weight, turn.token_weight),
                ("expiry", ref.expires_at, turn.absolute_expires_at),
            )
            for label, supplied, stored in bindings:
                if supplied != stored:
                    raise ModelStateError(
                        "state_binding_mismatch",
                        f"provider turn reference {label} does not match saved turn",
                        retryable=False,
                    )
            now = self._clock.now()
            if now >= turn.absolute_expires_at or now - turn.last_used_at > _IDLE_TTL:
                raise ModelStateError(
                    "state_expired",
                    "provider turn state expired",
                    retryable=False,
                )
            await connection.execute(
                _RENEW_TURN,
                (now, ref.turn_id, ref.scope_digest, ref.attempt_id),
            )
            await connection.commit()
            return turn.model_copy(update={"last_used_at": now})
        except ModelStateError:
            await _rollback_safely(connection)
            raise
        except (IndexError, TypeError, ValueError, ValidationError) as error:
            await _rollback_safely(connection)
            raise ModelStateError(
                "state_corrupt",
                "provider turn state is invalid",
                retryable=False,
            ) from error
        except psycopg.Error as error:
            await _rollback_safely(connection)
            raise ModelTransportError(
                "state_read_failed",
                "provider state read failed",
                retryable=True,
            ) from error
        finally:
            await connection.close()

    async def delete_attempt(self, attempt: AttemptRef) -> None:
        """Destroy payloads and mark every turn in one exact attempt terminal."""

        connection = await self._connect()
        try:
            await _begin_guarded_transaction(connection)
            await connection.execute(
                _DELETE_ATTEMPT,
                (self._clock.now(), attempt.scope_digest, attempt.attempt_id),
            )
            await connection.commit()
        except psycopg.Error as error:
            await _rollback_safely(connection)
            raise ModelTransportError(
                "state_delete_failed",
                "provider state deletion failed",
                retryable=True,
            ) from error
        finally:
            await connection.close()
