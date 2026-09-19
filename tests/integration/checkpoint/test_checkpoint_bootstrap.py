import os

import psycopg
import pytest

from scripts.bootstrap_runtime_state import checkpoint_object_manifest

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_checkpoint_bootstrap_matches_reviewed_manifest_and_owner() -> None:
    manifest = checkpoint_object_manifest()
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT c.relname, "
            "CASE c.relkind WHEN 'r' THEN 'table' WHEN 'i' THEN 'index' END, "
            "pg_get_userbyid(c.relowner) "
            "FROM pg_class AS c "
            "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'checkpoint' "
            "AND (c.relkind = 'r' OR (c.relkind = 'i' AND NOT EXISTS ("
            "SELECT 1 FROM pg_constraint AS k WHERE k.conindid = c.oid))) "
            "ORDER BY 2, 1"
        )
        observed = await cursor.fetchall()
        expected = sorted(
            [
                *((index, "index", "checkpoint_owner") for index in manifest.indexes),
                *((table, "table", "checkpoint_owner") for table in manifest.tables),
            ],
            key=lambda row: (row[1], row[0]),
        )
        assert observed == expected
    finally:
        await connection.close()
