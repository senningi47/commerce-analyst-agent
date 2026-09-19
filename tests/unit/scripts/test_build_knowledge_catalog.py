from collections import Counter
from pathlib import Path

import pytest

from commerce_agent.knowledge._catalog import canonical_catalog_bytes
from scripts.build_knowledge_catalog import build_catalog

REPO_ROOT = Path(__file__).resolve().parents[3]

# the builder reads the Olist raw CSVs from data/raw/, which git ignores —
# these tests can only run where the dataset has been imported locally
# (2026-09-19 backfill: a clean checkout must skip, not fail)
pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "data/raw/olistbr-brazilian-ecommerce-v2/files").is_dir(),
    reason="Olist raw dataset (gitignored) not present — import it locally to run the builder tests",
)


def test_builder_produces_reviewed_document_and_alias_counts() -> None:
    catalog = build_catalog(REPO_ROOT)
    kinds = Counter(document.kind.value for document in catalog.documents)
    domains = Counter(alias.domain.value for alias in catalog.aliases)

    assert len(catalog.documents) == 26
    assert kinds == {
        "table": 8,
        "join": 7,
        "data_quality": 4,
        "permission": 2,
        "metric": 5,
    }
    assert len(catalog.aliases) == 147
    assert domains == {
        "product_category": 71,
        "customer_state": 27,
        "seller_state": 23,
        "order_status": 16,
        "payment_type": 10,
    }


def test_builder_preserves_metric_and_schema_boundaries() -> None:
    catalog = build_catalog(REPO_ROOT)
    documents = {document.doc_id: document for document in catalog.documents}

    assert {doc_id for doc_id in documents if doc_id.startswith("table.")} == {
        "table.customers",
        "table.orders",
        "table.order_items",
        "table.order_payments",
        "table.order_reviews",
        "table.products",
        "table.sellers",
        "table.product_category_name_translation",
    }
    assert documents["metric.gmv"].content["status"] == "clarification_required"
    assert documents["metric.item_amount"].content["status"] == "active"
    assert documents["metric.item_amount"].content["includes_freight"] is False
    assert all(document.source_path for document in catalog.documents)
    assert all(len(document.source_sha256) == 64 for document in catalog.documents)


def test_builder_is_byte_deterministic_and_contains_no_bird_source() -> None:
    first = build_catalog(REPO_ROOT)
    second = build_catalog(REPO_ROOT)

    encoded = canonical_catalog_bytes(first)
    assert encoded == canonical_catalog_bytes(second)
    assert b"bird-interact" not in encoded.lower()
    assert b"evaluator" not in encoded.lower()
