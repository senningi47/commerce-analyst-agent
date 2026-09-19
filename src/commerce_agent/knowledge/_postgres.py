"""PostgreSQL KnowledgeStore over the profile-filtered safe view."""

from typing import Any

import psycopg
from pydantic import SecretStr, ValidationError

from commerce_agent.knowledge._store import StoredKnowledgeCatalog
from commerce_agent.knowledge.contracts import KnowledgeEvidence
from commerce_agent.knowledge.errors import (
    KnowledgeCatalogInvalid,
    KnowledgeError,
    KnowledgeInfrastructureError,
)

_SET_LOCAL_STATEMENTS = (
    "SET LOCAL statement_timeout = '5s'",
    "SET LOCAL lock_timeout = '1s'",
    "SET LOCAL idle_in_transaction_session_timeout = '10s'",
    "SET LOCAL work_mem = '16MB'",
    "SET LOCAL max_parallel_workers_per_gather = 0",
    "SET LOCAL search_path = knowledge",
)


async def _begin_guarded_transaction(connection: psycopg.AsyncConnection[Any]) -> None:
    await connection.execute("BEGIN READ ONLY")
    for statement in _SET_LOCAL_STATEMENTS:
        await connection.execute(statement)


class PostgresKnowledgeStore:
    def __init__(self, dsn: SecretStr) -> None:
        self._dsn = dsn

    async def load_retail_catalog(self) -> StoredKnowledgeCatalog | None:
        try:
            connection = await psycopg.AsyncConnection.connect(
                self._dsn.get_secret_value(),
                application_name="commerce_knowledge_reader",
                connect_timeout=3,
            )
        except psycopg.Error as error:
            raise KnowledgeInfrastructureError(
                "connection_failed",
                "Knowledge database connection failed",
            ) from error

        try:
            await _begin_guarded_transaction(connection)
            cursor = await connection.execute(
                "SELECT revision, catalog_sha256, doc_id, kind, title, content, "
                "source_type, source_path, source_sha256 FROM retail_documents "
                "ORDER BY kind, doc_id"
            )
            rows = await cursor.fetchall()
            if not rows:
                return None
            revisions = {(str(row[0]), str(row[1])) for row in rows}
            if len(revisions) != 1:
                raise KnowledgeCatalogInvalid(
                    "revision_shape",
                    "Knowledge view returned multiple active revisions",
                )
            revision, content_sha256 = next(iter(revisions))
            evidence = tuple(
                KnowledgeEvidence(
                    revision=str(row[0]),
                    doc_id=str(row[2]),
                    kind=str(row[3]),
                    title=str(row[4]),
                    content=row[5],
                    source_type=str(row[6]),
                    source_path=str(row[7]),
                    source_sha256=str(row[8]),
                )
                for row in rows
            )
            return StoredKnowledgeCatalog(
                revision=revision,
                content_sha256=content_sha256,
                evidence=evidence,
            )
        except KnowledgeError:
            raise
        except (IndexError, TypeError, ValueError, ValidationError) as error:
            raise KnowledgeCatalogInvalid(
                "result_shape",
                "Knowledge view returned an invalid result shape",
            ) from error
        except psycopg.Error as error:
            raise KnowledgeInfrastructureError(
                "postgres_error",
                "PostgreSQL rejected the Knowledge query",
            ) from error
        finally:
            try:
                await connection.rollback()
            finally:
                await connection.close()
