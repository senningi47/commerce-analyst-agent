"""Private reset port held only by deterministic Product evaluation."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, Self

import psycopg
from psycopg.conninfo import conninfo_to_dict
from pydantic import SecretStr

from commerce_agent.operations.errors import (
    OperationContractError,
    OperationInfrastructureError,
    OperationOutcomeUnknown,
)

ConnectionFactory = Callable[..., Awaitable[Any]]

_SCENARIO_ID = re.compile(r"^[a-z][a-z0-9-]{2,127}-v[0-9]+$")
_RESET_MANIFEST_REVISION = "scenario-reset-v1"
_TRANSACTION_STATEMENTS = (
    "SET LOCAL statement_timeout = '5s'",
    "SET LOCAL lock_timeout = '1s'",
    "SET LOCAL idle_in_transaction_session_timeout = '10s'",
)


class ScenarioResetPort(Protocol):
    async def reset(self, scenario_id: str, manifest_revision: str) -> None: ...


class InMemoryScenarioReset:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def reset(self, scenario_id: str, manifest_revision: str) -> None:
        self.calls.append((scenario_id, manifest_revision))


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


class PostgresScenarioReset:
    """Reset exactly one reviewed Product scenario through the reset role."""

    def __init__(
        self,
        dsn: SecretStr,
        *,
        connect: ConnectionFactory = psycopg.AsyncConnection.connect,
    ) -> None:
        self._dsn = dsn
        self._connect = connect

    async def open(self) -> Self:
        try:
            parameters = conninfo_to_dict(self._dsn.get_secret_value())
        except Exception as error:
            raise OperationInfrastructureError(
                "scenario_reset_database_identity_invalid", retryable=False
            ) from error
        required = {
            "user": "product_scenario_reset",
            "host": "127.0.0.1",
            "port": "5432",
            "dbname": "commerce_analyst",
            "application_name": "commerce_product_scenario_reset",
            "options": "-csearch_path=trusted_schema,ops,pg_catalog",
        }
        if any(parameters.get(key) != value for key, value in required.items()):
            raise OperationInfrastructureError(
                "scenario_reset_database_identity_invalid", retryable=False
            )
        return self

    async def reset(self, scenario_id: str, manifest_revision: str) -> None:
        if (
            not _SCENARIO_ID.fullmatch(scenario_id)
            or manifest_revision != _RESET_MANIFEST_REVISION
        ):
            raise OperationContractError("scenario_reset_scope_invalid", retryable=False)
        await self.open()
        try:
            connection = await self._connect(
                self._dsn.get_secret_value(), connect_timeout=3
            )
        except psycopg.Error as error:
            raise OperationInfrastructureError(
                "scenario_reset_connection_failed", retryable=True
            ) from error
        try:
            try:
                await connection.execute("BEGIN")
                for statement in _TRANSACTION_STATEMENTS:
                    await connection.execute(statement)
                await connection.execute(
                    "SELECT trusted_schema.reset_product_scenario(%s)",
                    (scenario_id,),
                )
            except psycopg.errors.SerializationFailure as error:
                await _rollback_quietly(connection)
                raise OperationInfrastructureError(
                    "scenario_reset_serialization_failure", retryable=True
                ) from error
            except psycopg.Error as error:
                await _rollback_quietly(connection)
                raise OperationInfrastructureError(
                    "scenario_reset_database_rejected", retryable=False
                ) from error
            except Exception:
                await _rollback_quietly(connection)
                raise
            try:
                await connection.commit()
            except psycopg.Error as error:
                raise OperationOutcomeUnknown(
                    "scenario_reset_commit_outcome_unknown", retryable=True
                ) from error
        finally:
            await _close_quietly(connection)
