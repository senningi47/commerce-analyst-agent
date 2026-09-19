import os

import psycopg
import pytest

pytestmark = pytest.mark.postgres


@pytest.fixture
async def runtime_probe_sequence() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        await connection.execute(
            "DROP SEQUENCE IF EXISTS model_state.permission_probe_sequence"
        )
        await connection.execute(
            "CREATE SEQUENCE model_state.permission_probe_sequence"
        )
        await connection.commit()
        yield
    finally:
        try:
            await connection.rollback()
            await connection.execute(
                "DROP SEQUENCE IF EXISTS model_state.permission_probe_sequence"
            )
            await connection.commit()
        finally:
            await connection.close()


@pytest.mark.asyncio
async def test_runtime_state_direct_acl_matrix_is_minimal() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT "
            "has_schema_privilege('model_state_writer', 'model_state', 'USAGE'), "
            "has_table_privilege('model_state_writer', 'model_state.provider_turn', "
            "'SELECT,INSERT,UPDATE,DELETE'), "
            "has_schema_privilege('model_state_writer', 'checkpoint', 'USAGE'), "
            "has_schema_privilege('checkpoint_writer', 'checkpoint', 'USAGE'), "
            "has_schema_privilege('checkpoint_writer', 'model_state', 'USAGE'), "
            "has_table_privilege('checkpoint_writer', 'model_state.provider_turn', 'SELECT'), "
            "has_schema_privilege('agent_reader', 'model_state', 'USAGE'), "
            "has_schema_privilege('agent_reader', 'checkpoint', 'USAGE'), "
            "has_schema_privilege('knowledge_reader', 'model_state', 'USAGE'), "
            "has_schema_privilege('knowledge_reader', 'checkpoint', 'USAGE')"
        )
        assert await cursor.fetchone() == (
            True,
            True,
            False,
            True,
            False,
            False,
            False,
            False,
            False,
            False,
        )
        cursor = await connection.execute(
            "SELECT "
            "has_database_privilege('model_state_writer', 'commerce_analyst', 'TEMP'), "
            "has_database_privilege('checkpoint_writer', 'commerce_analyst', 'TEMP'), "
            "has_schema_privilege('model_state_writer', 'model_state', 'CREATE'), "
            "has_schema_privilege('checkpoint_writer', 'checkpoint', 'CREATE'), "
            "has_table_privilege('model_state_writer', 'retail.orders', 'SELECT'), "
            "has_table_privilege('checkpoint_writer', 'retail.orders', 'SELECT'), "
            "has_table_privilege('model_state_writer', "
            "'ops_read.business_value_aliases', 'SELECT'), "
            "has_table_privilege('checkpoint_writer', "
            "'knowledge.retail_documents', 'SELECT')"
        )
        assert await cursor.fetchone() == (False,) * 8
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_runtime_state_roles_preserve_existing_reader_access_only() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT "
            "has_table_privilege('model_state_writer', 'retail.orders', 'SELECT'), "
            "has_table_privilege('model_state_writer', 'knowledge.document', 'SELECT'), "
            "has_table_privilege('model_state_writer', "
            "'knowledge.retail_documents', 'SELECT'), "
            "has_table_privilege('model_state_writer', "
            "'ops_read.business_value_aliases', 'SELECT'), "
            "has_table_privilege('checkpoint_writer', 'retail.orders', 'SELECT'), "
            "has_table_privilege('checkpoint_writer', 'knowledge.document', 'SELECT'), "
            "has_table_privilege('checkpoint_writer', "
            "'knowledge.retail_documents', 'SELECT'), "
            "has_table_privilege('checkpoint_writer', "
            "'ops_read.business_value_aliases', 'SELECT'), "
            "has_table_privilege('agent_reader', 'retail.orders', 'SELECT'), "
            "has_table_privilege('agent_reader', "
            "'ops_read.business_value_aliases', 'SELECT'), "
            "has_table_privilege('agent_reader', 'knowledge.document', 'SELECT'), "
            "has_table_privilege('agent_reader', "
            "'model_state.provider_turn', 'SELECT'), "
            "has_table_privilege('knowledge_reader', "
            "'knowledge.retail_documents', 'SELECT'), "
            "has_table_privilege('knowledge_reader', 'retail.orders', 'SELECT'), "
            "has_table_privilege('knowledge_reader', "
            "'model_state.provider_turn', 'SELECT')"
        )
        assert await cursor.fetchone() == (
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            True,
            True,
            False,
            False,
            True,
            False,
            False,
        )
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_runtime_state_role_attributes_are_hardened() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
            "rolreplication, rolbypassrls, rolconnlimit, "
            "coalesce('temp_file_limit=64MB' = ANY(rolconfig), false) "
            "FROM pg_roles WHERE rolname IN "
            "('checkpoint_owner', 'checkpoint_writer', "
            "'model_state_owner', 'model_state_writer') ORDER BY rolname"
        )
        assert await cursor.fetchall() == [
            ("checkpoint_owner", False, False, False, False, False, False, -1, False),
            ("checkpoint_writer", True, False, False, False, False, False, 4, True),
            ("model_state_owner", False, False, False, False, False, False, -1, False),
            ("model_state_writer", True, False, False, False, False, False, 4, True),
        ]
    finally:
        await connection.close()


@pytest.mark.parametrize(
    ("dsn_key", "statements"),
    [
        (
            "PRODUCT_MODEL_STATE_DATABASE_DSN",
            (
                "SELECT count(*) FROM retail.orders",
                "SELECT count(*) FROM knowledge.document",
                "SELECT count(*) FROM ops_read.business_value_aliases",
                "CREATE TABLE model_state.attack(value integer)",
                "CREATE TEMP TABLE escaped(value integer)",
            ),
        ),
        (
            "PRODUCT_CHECKPOINT_DATABASE_DSN",
            (
                "SELECT count(*) FROM model_state.provider_turn",
                "SELECT count(*) FROM retail.orders",
                "SELECT count(*) FROM knowledge.retail_documents",
                "CREATE TABLE checkpoint.attack(value integer)",
                "CREATE TEMP TABLE escaped(value integer)",
            ),
        ),
    ],
)
@pytest.mark.asyncio
async def test_runtime_writers_cannot_escape_object_privileges(
    dsn_key: str,
    statements: tuple[str, ...],
) -> None:
    for statement in statements:
        connection = await psycopg.AsyncConnection.connect(
            os.environ[dsn_key],
            autocommit=True,
        )
        try:
            await connection.execute("SET default_transaction_read_only = off")
            await connection.execute(
                "SET search_path = retail, knowledge, ops_read, model_state, checkpoint, public"
            )
            with pytest.raises(psycopg.Error) as caught:
                await connection.execute(statement)
            assert caught.value.sqlstate in {"25006", "42501"}
            assert os.environ[dsn_key] not in str(caught.value)
        finally:
            await connection.close()


@pytest.mark.parametrize(
    "dsn_key",
    ["PRODUCT_MODEL_STATE_DATABASE_DSN", "PRODUCT_CHECKPOINT_DATABASE_DSN"],
)
@pytest.mark.asyncio
async def test_runtime_writers_cannot_use_ungranted_sequences(
    dsn_key: str,
    runtime_probe_sequence: None,
) -> None:
    del runtime_probe_sequence
    connection = await psycopg.AsyncConnection.connect(os.environ[dsn_key])
    try:
        with pytest.raises(psycopg.Error) as caught:
            await connection.execute(
                "SELECT nextval('model_state.permission_probe_sequence')"
            )
        assert caught.value.sqlstate == "42501"
        assert os.environ[dsn_key] not in str(caught.value)
    finally:
        await connection.rollback()
        await connection.close()
