import os
from pathlib import Path

import psycopg
import pytest

from commerce_agent.knowledge._catalog import catalog_content_sha256, load_catalog_bytes
from scripts.import_knowledge import import_catalog

pytestmark = pytest.mark.postgres
CATALOG_PATH = Path("data/knowledge/retail_catalog.v1.json")


@pytest.mark.asyncio
async def test_one_active_revision_has_expected_counts_and_hashes() -> None:
    validated = load_catalog_bytes(CATALOG_PATH.read_bytes(), CATALOG_PATH.as_posix())
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT revision_id, content_sha256, data_manifest_sha256, "
            "(SELECT count(*) FROM knowledge.document AS d "
            "WHERE d.revision_id = r.revision_id), "
            "(SELECT count(*) FROM knowledge.value_alias AS a "
            "WHERE a.revision_id = r.revision_id) "
            "FROM knowledge.catalog_revision AS r WHERE active"
        )
        rows = await cursor.fetchall()
        assert rows == [
            (
                "retail-catalog-v1",
                catalog_content_sha256(validated),
                validated.catalog.data_manifest_sha256,
                26,
                147,
            )
        ]
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_safe_views_project_only_active_catalog() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT "
            "(SELECT count(*) FROM knowledge.retail_documents), "
            "(SELECT count(*) FROM ops_read.business_value_aliases), "
            "(SELECT count(*) FROM knowledge.retail_documents "
            "WHERE doc_id = 'metric.gmv' "
            "AND content->>'status' = 'clarification_required')"
        )
        assert await cursor.fetchone() == (26, 147, 1)
    finally:
        await connection.close()


def test_reimport_of_same_catalog_is_no_op() -> None:
    outcome = import_catalog(os.environ["PRODUCT_POSTGRES_ADMIN_DSN"], CATALOG_PATH)

    assert outcome.status == "no_op"
    assert outcome.document_count == 26
    assert outcome.alias_count == 147
