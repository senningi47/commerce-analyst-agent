import os

import psycopg
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_checkpoint_runtime_acl_matrix_is_minimal() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT "
            "has_schema_privilege('checkpoint_writer', 'checkpoint', 'USAGE'), "
            "has_schema_privilege('checkpoint_writer', 'checkpoint', 'CREATE'), "
            "has_table_privilege('checkpoint_writer', 'checkpoint.checkpoints', "
            "'SELECT,INSERT,UPDATE,DELETE'), "
            "has_table_privilege('checkpoint_writer', 'checkpoint.checkpoint_blobs', "
            "'SELECT,INSERT,UPDATE,DELETE'), "
            "has_table_privilege('checkpoint_writer', 'checkpoint.checkpoint_writes', "
            "'SELECT,INSERT,UPDATE,DELETE'), "
            "has_table_privilege('checkpoint_writer', "
            "'checkpoint.checkpoint_migrations', 'SELECT'), "
            "has_table_privilege('checkpoint_writer', "
            "'checkpoint.checkpoint_migrations', 'INSERT,UPDATE,DELETE'), "
            "has_database_privilege('checkpoint_writer', 'commerce_analyst', 'TEMP'), "
            "has_schema_privilege('checkpoint_writer', 'model_state', 'USAGE'), "
            "has_table_privilege('checkpoint_writer', "
            "'model_state.provider_turn', 'SELECT'), "
            "has_table_privilege('checkpoint_writer', 'retail.orders', 'SELECT'), "
            "has_table_privilege('checkpoint_writer', "
            "'knowledge.retail_documents', 'SELECT'), "
            "has_table_privilege('checkpoint_writer', "
            "'ops_read.business_value_aliases', 'SELECT')"
        )
        assert await cursor.fetchone() == (
            True,
            False,
            True,
            True,
            True,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
        )
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_checkpoint_writer_cannot_run_library_setup() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_CHECKPOINT_DATABASE_DSN"],
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    try:
        saver = AsyncPostgresSaver(connection)
        with pytest.raises(psycopg.Error) as caught:
            await saver.setup()
        assert caught.value.sqlstate == "42501"
        assert os.environ["PRODUCT_CHECKPOINT_DATABASE_DSN"] not in str(caught.value)
    finally:
        await connection.close()


@pytest.fixture
async def checkpoint_probe_sequence() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"],
        autocommit=True,
    )
    try:
        await connection.execute(
            "DROP SEQUENCE IF EXISTS checkpoint.permission_probe_sequence"
        )
        await connection.execute("CREATE SEQUENCE checkpoint.permission_probe_sequence")
        yield
    finally:
        await connection.execute(
            "DROP SEQUENCE IF EXISTS checkpoint.permission_probe_sequence"
        )
        await connection.close()


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM checkpoint.checkpoint_migrations",
        "CREATE TABLE checkpoint.attack(value integer)",
        "CREATE TEMP TABLE escaped(value integer)",
        "SELECT nextval('checkpoint.permission_probe_sequence')",
        "SELECT count(*) FROM model_state.provider_turn",
        "SELECT count(*) FROM retail.orders",
        "SELECT count(*) FROM knowledge.retail_documents",
        "SELECT count(*) FROM ops_read.business_value_aliases",
    ],
)
@pytest.mark.asyncio
async def test_checkpoint_writer_cannot_escape_runtime_privileges(
    statement: str,
    checkpoint_probe_sequence: None,
) -> None:
    del checkpoint_probe_sequence
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_CHECKPOINT_DATABASE_DSN"],
        autocommit=True,
    )
    try:
        await connection.execute("SET default_transaction_read_only = off")
        await connection.execute(
            "SET search_path = checkpoint, model_state, retail, knowledge, ops_read, public"
        )
        with pytest.raises(psycopg.Error) as caught:
            await connection.execute(statement)
        assert caught.value.sqlstate in {"25006", "42501"}
        assert os.environ["PRODUCT_CHECKPOINT_DATABASE_DSN"] not in str(caught.value)
    finally:
        await connection.close()


@pytest.mark.parametrize(
    "dsn_key",
    [
        "PRODUCT_MODEL_STATE_DATABASE_DSN",
        "PRODUCT_DATABASE_DSN",
        "PRODUCT_KNOWLEDGE_DATABASE_DSN",
    ],
)
@pytest.mark.asyncio
async def test_other_runtime_roles_cannot_read_checkpoint_state(dsn_key: str) -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ[dsn_key],
        autocommit=True,
    )
    try:
        with pytest.raises(psycopg.Error) as caught:
            await connection.execute("SELECT count(*) FROM checkpoint.checkpoints")
        assert caught.value.sqlstate == "42501"
        assert os.environ[dsn_key] not in str(caught.value)
    finally:
        await connection.close()
