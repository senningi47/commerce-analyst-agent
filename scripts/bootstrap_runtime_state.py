"""Create runtime-state roles before controlled schema bootstrap."""

import argparse
import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import sql
from psycopg.rows import dict_row
from pydantic import SecretStr

_CHECKPOINT_PACKAGE = "langgraph-checkpoint-postgres"
_CHECKPOINT_PACKAGE_VERSION = "3.1.2"


@dataclass(frozen=True)
class CheckpointObjectManifest:
    package: str
    package_version: str
    tables: tuple[str, ...]
    indexes: tuple[str, ...]


_CHECKPOINT_OBJECT_MANIFEST = CheckpointObjectManifest(
    package=_CHECKPOINT_PACKAGE,
    package_version=_CHECKPOINT_PACKAGE_VERSION,
    tables=(
        "checkpoint_migrations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
    ),
    indexes=(
        "checkpoints_thread_id_idx",
        "checkpoint_blobs_thread_id_idx",
        "checkpoint_writes_thread_id_idx",
    ),
)


class RuntimeStateBootstrapError(RuntimeError):
    """A controlled runtime-state bootstrap operation failed safely."""

    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        message = "runtime-state bootstrap failed"
        if detail is not None:
            message = f"{message}: {detail}"
        super().__init__(message)
        self.reason_code = reason_code


def checkpoint_object_manifest() -> CheckpointObjectManifest:
    """Return the reviewed object manifest for the pinned checkpoint package."""

    if version(_CHECKPOINT_PACKAGE) != _CHECKPOINT_PACKAGE_VERSION:
        raise RuntimeStateBootstrapError("checkpoint_package_version_mismatch")
    return _CHECKPOINT_OBJECT_MANIFEST


async def setup_checkpoint_objects(
    admin_dsn: SecretStr,
    *,
    connect: Callable[..., Awaitable[Any]] = psycopg.AsyncConnection.connect,
) -> None:
    """Create pinned checkpoint objects through the controlled admin path."""

    if not admin_dsn.get_secret_value():
        raise ValueError("PRODUCT_POSTGRES_ADMIN_DSN must not be empty")
    checkpoint_object_manifest()
    connection = await connect(
        admin_dsn.get_secret_value(),
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
        options="-csearch_path=checkpoint,pg_catalog",
    )
    async with connection:
        saver = AsyncPostgresSaver(connection)
        await saver.setup()


async def apply_runtime_acls(
    admin_dsn: SecretStr,
    *,
    connect: Callable[..., Awaitable[Any]] = psycopg.AsyncConnection.connect,
) -> None:
    """Verify pinned checkpoint objects before applying runtime ownership and ACLs."""

    if not admin_dsn.get_secret_value():
        raise ValueError("PRODUCT_POSTGRES_ADMIN_DSN must not be empty")
    manifest = checkpoint_object_manifest()
    connection = await connect(
        admin_dsn.get_secret_value(),
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
        options="-csearch_path=checkpoint,pg_catalog",
    )
    async with connection, connection.transaction(), connection.cursor() as cursor:
        await cursor.execute(
            "SELECT c.relname AS object_name, "
            "CASE c.relkind WHEN 'r' THEN 'table' WHEN 'i' THEN 'index' END "
            "AS object_type "
            "FROM pg_class AS c "
            "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'checkpoint' "
            "AND (c.relkind = 'r' OR (c.relkind = 'i' AND NOT EXISTS ("
            "SELECT 1 FROM pg_constraint AS k WHERE k.conindid = c.oid))) "
            "ORDER BY object_type, object_name"
        )
        rows = await cursor.fetchall()
        actual = {(row["object_type"], row["object_name"]) for row in rows}
        expected = {
            *(("table", table) for table in manifest.tables),
            *(("index", index) for index in manifest.indexes),
        }
        if actual != expected:
            unexpected = sorted(actual - expected)
            detail = (
                f"unexpected {unexpected[0][0]} {unexpected[0][1]}"
                if unexpected
                else "required checkpoint object missing"
            )
            raise RuntimeStateBootstrapError("checkpoint_manifest_mismatch", detail)

        for table in manifest.tables:
            await cursor.execute(
                sql.SQL("ALTER TABLE {}.{} OWNER TO {}").format(
                    sql.Identifier("checkpoint"),
                    sql.Identifier(table),
                    sql.Identifier("checkpoint_owner"),
                )
            )
        await cursor.execute("REVOKE ALL ON SCHEMA checkpoint FROM PUBLIC")
        await cursor.execute(
            "REVOKE ALL ON SCHEMA checkpoint FROM agent_reader, knowledge_reader, "
            "model_state_writer, checkpoint_writer"
        )
        await cursor.execute(
            "REVOKE ALL ON TABLE checkpoint.checkpoint_migrations, "
            "checkpoint.checkpoints, checkpoint.checkpoint_blobs, "
            "checkpoint.checkpoint_writes FROM PUBLIC, agent_reader, knowledge_reader, "
            "model_state_writer, checkpoint_writer"
        )
        await cursor.execute(
            "REVOKE ALL ON ALL SEQUENCES IN SCHEMA checkpoint FROM PUBLIC, "
            "agent_reader, knowledge_reader, model_state_writer, checkpoint_writer"
        )
        await cursor.execute("GRANT USAGE ON SCHEMA checkpoint TO checkpoint_writer")
        await cursor.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
            "checkpoint.checkpoints, checkpoint.checkpoint_blobs, "
            "checkpoint.checkpoint_writes TO checkpoint_writer"
        )
        await cursor.execute(
            "REVOKE CREATE ON SCHEMA checkpoint FROM checkpoint_writer"
        )
        await cursor.execute(
            "REVOKE TEMPORARY ON DATABASE commerce_analyst FROM checkpoint_writer"
        )


def _role_exists(cursor: psycopg.Cursor[Any], role_name: str) -> bool:
    cursor.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)",
        (role_name,),
    )
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("PostgreSQL did not return a role-existence result")
    return bool(row[0])


def bootstrap_runtime_roles(
    admin_dsn: str,
    checkpoint_writer_password: str,
    model_state_writer_password: str,
) -> None:
    """Create or harden the isolated checkpoint and model-state roles."""

    if not admin_dsn:
        raise ValueError("PRODUCT_POSTGRES_ADMIN_DSN must not be empty")
    if not checkpoint_writer_password:
        raise ValueError("PRODUCT_CHECKPOINT_WRITER_PASSWORD must not be empty")
    if not model_state_writer_password:
        raise ValueError("PRODUCT_MODEL_STATE_WRITER_PASSWORD must not be empty")
    if checkpoint_writer_password == model_state_writer_password:
        raise ValueError("runtime-state writer passwords must differ")

    with (
        psycopg.connect(admin_dsn, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        for owner_role in ("checkpoint_owner", "model_state_owner"):
            identifier = sql.Identifier(owner_role)
            action = "ALTER" if _role_exists(cursor, owner_role) else "CREATE"
            cursor.execute(
                sql.SQL(
                    f"{action} ROLE {{}} NOLOGIN NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                ).format(identifier)
            )

        for writer_role, password in (
            ("checkpoint_writer", checkpoint_writer_password),
            ("model_state_writer", model_state_writer_password),
        ):
            identifier = sql.Identifier(writer_role)
            password_literal = sql.Literal(password)
            action = "ALTER" if _role_exists(cursor, writer_role) else "CREATE"
            cursor.execute(
                sql.SQL(
                    f"{action} ROLE {{}} LOGIN NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(identifier, password_literal)
            )
            cursor.execute(
                sql.SQL("ALTER ROLE {} CONNECTION LIMIT 4").format(identifier)
            )
            cursor.execute(
                sql.SQL("ALTER ROLE {} SET temp_file_limit = '64MB'").format(identifier)
            )

        cursor.execute(
            "GRANT CONNECT ON DATABASE commerce_analyst TO "
            "checkpoint_writer, model_state_writer"
        )
        cursor.execute(
            "REVOKE TEMPORARY ON DATABASE commerce_analyst FROM "
            "checkpoint_writer, model_state_writer"
        )


async def _bootstrap_checkpoint(admin_dsn: SecretStr) -> None:
    await setup_checkpoint_objects(admin_dsn)
    await apply_runtime_acls(admin_dsn)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("roles", "checkpoint"))
    command = parser.parse_args(argv).command

    if command == "roles":
        bootstrap_runtime_roles(
            admin_dsn=os.environ["PRODUCT_POSTGRES_ADMIN_DSN"],
            checkpoint_writer_password=os.environ[
                "PRODUCT_CHECKPOINT_WRITER_PASSWORD"
            ],
            model_state_writer_password=os.environ[
                "PRODUCT_MODEL_STATE_WRITER_PASSWORD"
            ],
        )
        print("Runtime state roles bootstrapped")
        return

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(
        _bootstrap_checkpoint(SecretStr(os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]))
    )
    print("Checkpoint objects and ACLs bootstrapped")


if __name__ == "__main__":
    main()
