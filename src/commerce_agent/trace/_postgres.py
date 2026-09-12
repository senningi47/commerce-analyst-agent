"""Append-only role-bound PostgreSQL Product Trace adapter."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, Self
from uuid import NAMESPACE_URL, uuid5

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from commerce_agent.trace.contracts import ScopedTraceEvent
from commerce_agent.trace.errors import TraceContractError
from commerce_agent.trace.module import validate_trace_event

ConnectionFactory = Callable[..., Awaitable[Any]]

_SCENARIO_ID = re.compile(r"^[a-z][a-z0-9-]{2,127}-v[0-9]+$")
_TRANSACTION_STATEMENTS = (
    "SET LOCAL statement_timeout = '5s'",
    "SET LOCAL lock_timeout = '1s'",
    "SET LOCAL idle_in_transaction_session_timeout = '10s'",
)


class TraceInfrastructureError(TraceContractError):
    """A sanitized Product Trace persistence failure."""


def _validate_dsn(dsn: SecretStr) -> None:
    try:
        parameters = conninfo_to_dict(dsn.get_secret_value())
    except Exception as error:
        raise TraceInfrastructureError(
            "trace_database_identity_invalid", retryable=False
        ) from error
    required = {
        "user": "trace_writer",
        "host": "127.0.0.1",
        "port": "5432",
        "dbname": "commerce_analyst",
        "application_name": "commerce_product_trace",
        "options": "-csearch_path=app,pg_catalog",
    }
    if any(parameters.get(key) != value for key, value in required.items()):
        raise TraceInfrastructureError(
            "trace_database_identity_invalid", retryable=False
        )


async def _rollback_quietly(connection: Any) -> None:
    try:
        await connection.rollback()
    except psycopg.Error:
        return


async def _close_quietly(connection: Any) -> None:
    try:
        await connection.close()
    except psycopg.Error:
        return


def _run_scope_digest(event: ScopedTraceEvent) -> str:
    encoded = json.dumps(
        event.run_scope.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_summary(event: ScopedTraceEvent) -> dict[str, object]:
    return {
        "audit_ref": event.audit_ref,
        "cost": event.cost.model_dump(mode="json") if event.cost else None,
        "decision_summary": (
            event.decision_summary.model_dump(mode="json")
            if event.decision_summary
            else None
        ),
        "evidence": [item.model_dump(mode="json") for item in event.evidence],
        "execution_ref": event.execution_ref,
        "proposal_ref": event.proposal_ref,
        "query_fingerprints": list(event.query_fingerprints),
        "usage": event.usage.model_dump(mode="json") if event.usage else None,
    }


class PostgresTraceStore:
    """TracePort that exposes append only under the trace-writer identity."""

    def __init__(
        self,
        dsn: SecretStr,
        *,
        scenario_id: str,
        connect: ConnectionFactory = psycopg.AsyncConnection.connect,
    ) -> None:
        self._dsn = dsn
        self._scenario_id = scenario_id
        self._connect = connect

    async def open(self) -> Self:
        _validate_dsn(self._dsn)
        if not _SCENARIO_ID.fullmatch(self._scenario_id):
            raise TraceInfrastructureError(
                "trace_scenario_identity_invalid", retryable=False
            )
        return self

    async def append(self, event: ScopedTraceEvent) -> None:
        await self.open()
        if (event.run_scope.track, event.run_scope.mode) != ("retail", "retail"):
            raise TraceContractError("trace_scope_forbidden")
        validate_trace_event(event)
        digest = _run_scope_digest(event)
        event_id = uuid5(
            NAMESPACE_URL,
            f"product-trace:{digest}:{event.attempt_id}:{event.sequence}",
        )
        try:
            connection = await self._connect(
                self._dsn.get_secret_value(), connect_timeout=3
            )
        except psycopg.Error as error:
            raise TraceInfrastructureError(
                "trace_connection_failed", retryable=True
            ) from error

        try:
            try:
                await connection.execute("BEGIN")
                for statement in _TRANSACTION_STATEMENTS:
                    await connection.execute(statement)
                await connection.execute(
                    "INSERT INTO app.product_trace_event ("
                    "event_id, run_scope_digest, run_id, attempt_id, sequence, phase, node, "
                    "event_type, status, safe_summary, config_hash, prompt_policy_hash, "
                    "rendered_prompt_hash, model_hash, tool_hash, context_hash, reason_code, "
                    "occurred_at, scenario_id) VALUES ("
                    + ", ".join(["%s"] * 19)
                    + ")",
                    (
                        event_id,
                        digest,
                        event.run_scope.run_id,
                        event.attempt_id,
                        event.sequence,
                        event.phase,
                        event.node,
                        event.event_type.value,
                        event.status.value,
                        Jsonb(_safe_summary(event)),
                        event.config_hash,
                        event.prompt_policy_hash,
                        event.rendered_prompt_hash,
                        event.model_hash,
                        event.tool_hash,
                        event.context_hash,
                        event.reason_code,
                        event.occurred_at,
                        self._scenario_id,
                    ),
                )
            except psycopg.errors.SerializationFailure as error:
                await _rollback_quietly(connection)
                raise TraceInfrastructureError(
                    "trace_serialization_failure", retryable=True
                ) from error
            except psycopg.Error as error:
                await _rollback_quietly(connection)
                raise TraceInfrastructureError(
                    "trace_database_rejected", retryable=False
                ) from error
            except Exception:
                await _rollback_quietly(connection)
                raise

            try:
                await connection.commit()
            except psycopg.Error as error:
                raise TraceInfrastructureError(
                    "trace_commit_outcome_unknown", retryable=True
                ) from error
        finally:
            await _close_quietly(connection)
