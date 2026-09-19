import os

import psycopg
import pytest

pytestmark = pytest.mark.postgres


def assert_agent_dsn_is_sanitized(error: BaseException) -> None:
    dsn = os.environ["PRODUCT_DATABASE_DSN"]
    assert dsn not in str(error)
    assert dsn not in repr(error)


@pytest.mark.asyncio
async def test_agent_reader_can_read_alias_projection_but_not_raw_knowledge() -> None:
    connection = await psycopg.AsyncConnection.connect(os.environ["PRODUCT_DATABASE_DSN"])
    try:
        cursor = await connection.execute(
            "SELECT count(*) FROM ops_read.business_value_aliases"
        )
        assert (await cursor.fetchone())[0] == 147
        for statement in (
            "SELECT count(*) FROM knowledge.catalog_revision",
            "SELECT count(*) FROM knowledge.document",
            "SELECT count(*) FROM knowledge.value_alias",
        ):
            with pytest.raises(psycopg.Error) as caught:
                await connection.execute(statement)
            assert caught.value.sqlstate == "42501"
            assert_agent_dsn_is_sanitized(caught.value)
            await connection.rollback()
    finally:
        await connection.rollback()
        await connection.close()


@pytest.mark.asyncio
async def test_agent_reader_cannot_modify_alias_projection() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_DATABASE_DSN"],
        autocommit=True,
    )
    try:
        await connection.execute("SET default_transaction_read_only = off")
        with pytest.raises(psycopg.Error) as caught:
            await connection.execute(
                "UPDATE ops_read.business_value_aliases "
                "SET display_label = display_label WHERE false"
            )
        assert caught.value.sqlstate in {"42501", "55000"}
        assert_agent_dsn_is_sanitized(caught.value)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_runtime_roles_have_no_sequence_privilege() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        await connection.execute("DROP SEQUENCE IF EXISTS knowledge.permission_probe_sequence")
        await connection.execute("CREATE SEQUENCE knowledge.permission_probe_sequence")
        await connection.commit()
        cursor = await connection.execute(
            "SELECT "
            "has_sequence_privilege("
            "'agent_reader', 'knowledge.permission_probe_sequence', 'USAGE'"
            "), "
            "has_sequence_privilege("
            "'knowledge_reader', 'knowledge.permission_probe_sequence', 'USAGE'"
            ")"
        )
        assert await cursor.fetchone() == (False, False)
    finally:
        try:
            await connection.rollback()
            await connection.execute(
                "DROP SEQUENCE IF EXISTS knowledge.permission_probe_sequence"
            )
            await connection.commit()
        finally:
            await connection.close()
