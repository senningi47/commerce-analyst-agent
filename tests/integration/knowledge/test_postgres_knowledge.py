import json
import os
from pathlib import Path

import pytest
from pydantic import SecretStr

from commerce_agent.knowledge._catalog import catalog_content_sha256, load_catalog_bytes
from commerce_agent.knowledge._postgres import PostgresKnowledgeStore
from commerce_agent.knowledge.contracts import KnowledgeRequest
from commerce_agent.knowledge.module import KnowledgeModule
from tests.contracts.knowledge import assert_bundle_contract

pytestmark = pytest.mark.postgres
CATALOG_PATH = Path("data/knowledge/retail_catalog.v1.json")


@pytest.mark.asyncio
async def test_postgres_knowledge_returns_complete_reviewed_catalog() -> None:
    module = KnowledgeModule(
        PostgresKnowledgeStore(
            SecretStr(os.environ["PRODUCT_KNOWLEDGE_DATABASE_DSN"])
        )
    )

    bundle = await module.retrieve(KnowledgeRequest(question="GMV and joins"))

    assert_bundle_contract(bundle)
    assert len(bundle.evidence) == 26
    ids = {item.doc_id for item in bundle.evidence}
    assert len({doc_id for doc_id in ids if doc_id.startswith("table.")}) == 8
    assert len({doc_id for doc_id in ids if doc_id.startswith("join.")}) == 7
    assert {"metric.item_amount", "metric.freight_amount", "metric.payment_amount"} < ids
    encoded = json.dumps(
        [item.model_dump(mode="json") for item in bundle.evidence],
        ensure_ascii=False,
    ).casefold()
    assert "evaluator" not in encoded
    assert "bird-interact" not in encoded


@pytest.mark.asyncio
async def test_postgres_knowledge_revision_matches_checked_in_catalog() -> None:
    validated = load_catalog_bytes(CATALOG_PATH.read_bytes(), CATALOG_PATH.as_posix())
    module = KnowledgeModule(
        PostgresKnowledgeStore(
            SecretStr(os.environ["PRODUCT_KNOWLEDGE_DATABASE_DSN"])
        )
    )

    bundle = await module.retrieve(KnowledgeRequest(question="schema"))

    assert bundle.catalog_revision == validated.catalog.revision_id
    assert bundle.catalog_sha256 == catalog_content_sha256(validated)
