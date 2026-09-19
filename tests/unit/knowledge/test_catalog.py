import copy
import json

import pytest

from commerce_agent.knowledge._catalog import (
    canonical_catalog_bytes,
    catalog_content_sha256,
    load_catalog_bytes,
)
from commerce_agent.knowledge.errors import KnowledgeCatalogInvalid

SOURCE_SHA256 = "a" * 64


def minimal_catalog() -> dict[str, object]:
    return {
        "schema_version": 1,
        "revision_id": "retail-catalog-v1",
        "data_manifest_sha256": "f" * 64,
        "documents": [
            {
                "doc_id": "table.products",
                "kind": "table",
                "title": "Products",
                "content": {"columns": ["product_category_name"]},
                "source_type": "migration",
                "source_path": "db/migrations/versions/0001_retail_schema.py",
                "source_sha256": SOURCE_SHA256,
                "allowed_profiles": ["retail"],
            },
            {
                "doc_id": "metric.gmv",
                "kind": "metric",
                "title": "GMV",
                "content": {"status": "clarification_required"},
                "source_type": "design_spec",
                "source_path": "docs/project/specs/day-2b.md",
                "source_sha256": SOURCE_SHA256,
                "allowed_profiles": ["retail"],
            },
        ],
        "aliases": [
            {
                "domain": "product_category",
                "normalized_alias": "health_beauty",
                "canonical_value": "beleza_saude",
                "display_label": "Beleza e saúde",
                "locale": "en",
                "source_doc_id": "table.products",
            }
        ],
    }


def load(payload: dict[str, object]):
    return load_catalog_bytes(json.dumps(payload).encode(), "catalog.json")


def test_canonical_hash_ignores_json_formatting_and_object_key_order() -> None:
    payload = minimal_catalog()
    reversed_payload = dict(reversed(list(payload.items())))
    compact = json.dumps(payload, separators=(",", ":")).encode()
    pretty = json.dumps(reversed_payload, indent=2).encode()

    compact_catalog = load_catalog_bytes(compact, "compact.json")
    pretty_catalog = load_catalog_bytes(pretty, "pretty.json")

    assert catalog_content_sha256(compact_catalog) == catalog_content_sha256(pretty_catalog)
    assert canonical_catalog_bytes(compact_catalog) == canonical_catalog_bytes(pretty_catalog)


def test_duplicate_document_ids_are_rejected() -> None:
    payload = minimal_catalog()
    payload["documents"].append(copy.deepcopy(payload["documents"][0]))

    with pytest.raises(KnowledgeCatalogInvalid) as caught:
        load(payload)

    assert caught.value.reason_code == "duplicate_doc_id"


def test_duplicate_alias_keys_are_rejected() -> None:
    payload = minimal_catalog()
    duplicate = copy.deepcopy(payload["aliases"][0])
    duplicate["canonical_value"] = "another_value"
    payload["aliases"].append(duplicate)

    with pytest.raises(KnowledgeCatalogInvalid) as caught:
        load(payload)

    assert caught.value.reason_code == "duplicate_alias"


def test_alias_source_must_reference_document_in_revision() -> None:
    payload = minimal_catalog()
    payload["aliases"][0]["source_doc_id"] = "table.missing"

    with pytest.raises(KnowledgeCatalogInvalid) as caught:
        load(payload)

    assert caught.value.reason_code == "alias_source_missing"


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        (lambda payload: payload["documents"][0].update(source_sha256="bad"), "catalog_shape"),
        (lambda payload: payload.update(data_manifest_sha256="bad"), "catalog_shape"),
        (
            lambda payload: payload["documents"][1]["content"].update(status="active"),
            "gmv_definition",
        ),
        (lambda payload: payload["documents"][1].update(doc_id="metric.revenue"), "gmv_missing"),
        (lambda payload: payload["documents"][0].update(allowed_profiles=[]), "catalog_shape"),
        (lambda payload: payload["aliases"][0].update(domain="customer_name"), "catalog_shape"),
    ],
)
def test_invalid_catalogs_fail_closed(mutation, reason_code: str) -> None:
    payload = minimal_catalog()
    mutation(payload)

    with pytest.raises(KnowledgeCatalogInvalid) as caught:
        load(payload)

    assert caught.value.reason_code == reason_code
