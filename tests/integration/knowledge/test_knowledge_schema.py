import os

import psycopg
import pytest

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_knowledge_objects_exist_before_import() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT "
            "to_regclass('knowledge.catalog_revision'), "
            "to_regclass('knowledge.document'), "
            "to_regclass('knowledge.value_alias'), "
            "to_regclass('knowledge.retail_documents'), "
            "to_regclass('ops_read.business_value_aliases')"
        )
        assert await cursor.fetchone() == (
            "knowledge.catalog_revision",
            "knowledge.document",
            "knowledge.value_alias",
            "knowledge.retail_documents",
            "ops_read.business_value_aliases",
        )
        cursor = await connection.execute(
            "SELECT count(*) FROM knowledge.catalog_revision WHERE active"
        )
        assert (await cursor.fetchone())[0] <= 1
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_knowledge_roles_and_direct_acl_are_minimal() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT "
            "has_schema_privilege('agent_reader', 'knowledge', 'USAGE'), "
            "has_table_privilege('agent_reader', 'knowledge.document', 'SELECT'), "
            "has_table_privilege("
            "'agent_reader', 'ops_read.business_value_aliases', 'SELECT'"
            "), "
            "has_schema_privilege('knowledge_reader', 'knowledge', 'USAGE'), "
            "has_table_privilege("
            "'knowledge_reader', 'knowledge.document', 'SELECT'"
            "), "
            "has_table_privilege("
            "'knowledge_reader', 'knowledge.retail_documents', 'SELECT'"
            "), "
            "has_table_privilege('knowledge_reader', 'retail.orders', 'SELECT')"
        )
        assert await cursor.fetchone() == (False, False, True, True, False, True, False)

        cursor = await connection.execute(
            "SELECT "
            "has_database_privilege('agent_reader', 'commerce_analyst', 'TEMP'), "
            "has_database_privilege('knowledge_reader', 'commerce_analyst', 'TEMP'), "
            "has_schema_privilege('agent_reader', 'ops_read', 'CREATE'), "
            "has_schema_privilege('knowledge_reader', 'knowledge', 'CREATE')"
        )
        assert await cursor.fetchone() == (False, False, False, False)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_knowledge_view_owners_and_security_barriers_are_fixed() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT c.relname, pg_get_userbyid(c.relowner), c.reloptions "
            "FROM pg_class AS c "
            "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "WHERE (n.nspname, c.relname) IN ("
            "('knowledge', 'retail_documents'), "
            "('ops_read', 'business_value_aliases')) "
            "ORDER BY c.relname"
        )
        assert await cursor.fetchall() == [
            ("business_value_aliases", "retail_owner", ["security_barrier=true"]),
            ("retail_documents", "knowledge_owner", ["security_barrier=true"]),
        ]
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_knowledge_reader_role_attributes_are_hardened() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
            "rolreplication, rolbypassrls, rolconnlimit "
            "FROM pg_roles WHERE rolname = 'knowledge_reader'"
        )
        assert await cursor.fetchone() == (True, False, False, False, False, False, 4)
    finally:
        await connection.close()
