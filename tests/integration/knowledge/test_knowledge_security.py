import os

import psycopg
import pytest

pytestmark = pytest.mark.postgres


def assert_knowledge_dsn_is_sanitized(error: BaseException) -> None:
    dsn = os.environ["PRODUCT_KNOWLEDGE_DATABASE_DSN"]
    assert dsn not in str(error)
    assert dsn not in repr(error)


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT count(*) FROM knowledge.document",
        "SELECT count(*) FROM knowledge.value_alias",
        "SELECT count(*) FROM retail.orders",
        "SELECT count(*) FROM ops_read.business_value_aliases",
    ],
)
@pytest.mark.asyncio
async def test_knowledge_reader_cannot_read_raw_or_cross_domain_tables(
    statement: str,
) -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_KNOWLEDGE_DATABASE_DSN"]
    )
    try:
        with pytest.raises(psycopg.Error) as caught:
            await connection.execute(statement)
        assert caught.value.sqlstate == "42501"
        assert_knowledge_dsn_is_sanitized(caught.value)
    finally:
        await connection.rollback()
        await connection.close()


@pytest.mark.asyncio
async def test_knowledge_reader_can_read_only_safe_view() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_KNOWLEDGE_DATABASE_DSN"]
    )
    try:
        cursor = await connection.execute("SELECT count(*) FROM knowledge.retail_documents")
        assert (await cursor.fetchone())[0] == 26
    finally:
        await connection.rollback()
        await connection.close()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE knowledge.document SET title = title WHERE false",
        (
            "INSERT INTO knowledge.catalog_revision "
            "(revision_id, schema_version, content_sha256, source_path, source_sha256, "
            "data_manifest_sha256, active) VALUES "
            "('attack', 1, repeat('a',64), 'x', repeat('b',64), repeat('c',64), false)"
        ),
        "CREATE TABLE knowledge.attack(value integer)",
    ],
)
@pytest.mark.asyncio
async def test_knowledge_reader_acl_rejects_writes_after_disabling_default(
    statement: str,
) -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_KNOWLEDGE_DATABASE_DSN"],
        autocommit=True,
    )
    try:
        await connection.execute("SET default_transaction_read_only = off")
        with pytest.raises(psycopg.Error) as caught:
            await connection.execute(statement)
        assert caught.value.sqlstate == "42501"
        assert_knowledge_dsn_is_sanitized(caught.value)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_knowledge_reader_cannot_create_temp_tables() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_KNOWLEDGE_DATABASE_DSN"]
    )
    try:
        with pytest.raises(psycopg.Error) as caught:
            await connection.execute("CREATE TEMP TABLE escaped(value integer)")
        assert caught.value.sqlstate in {"25006", "42501"}
        assert_knowledge_dsn_is_sanitized(caught.value)
    finally:
        await connection.rollback()
        await connection.close()


@pytest.mark.asyncio
async def test_knowledge_reader_role_defaults_are_fixed() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_KNOWLEDGE_DATABASE_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT current_setting('default_transaction_read_only'), "
            "current_setting('temp_file_limit')"
        )
        assert await cursor.fetchone() == ("on", "64MB")
    finally:
        await connection.close()
