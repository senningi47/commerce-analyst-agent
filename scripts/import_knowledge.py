"""Import and atomically activate a validated Knowledge catalog."""

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

from commerce_agent.knowledge._catalog import (
    CatalogFile,
    ValidatedCatalog,
    catalog_content_sha256,
    load_catalog_bytes,
)
from commerce_agent.knowledge.errors import KnowledgeCatalogInvalid


@dataclass(frozen=True)
class ImportOutcome:
    status: Literal["inserted", "no_op"]
    revision_id: str
    source_sha256: str
    content_sha256: str
    document_count: int
    alias_count: int


_DOMAIN_QUERIES = {
    "customer_state": "SELECT DISTINCT customer_state FROM retail.customers WHERE customer_state = ANY(%s)",
    "seller_state": "SELECT DISTINCT seller_state FROM retail.sellers WHERE seller_state = ANY(%s)",
    "order_status": "SELECT DISTINCT order_status FROM retail.orders WHERE order_status = ANY(%s)",
    "payment_type": "SELECT DISTINCT payment_type FROM retail.order_payments WHERE payment_type = ANY(%s)",
    "product_category": (
        "SELECT DISTINCT product_category_name "
        "FROM retail.product_category_name_translation "
        "WHERE product_category_name = ANY(%s)"
    ),
    "customer_city": "SELECT DISTINCT customer_city FROM retail.customers WHERE customer_city = ANY(%s)",
    "seller_city": "SELECT DISTINCT seller_city FROM retail.sellers WHERE seller_city = ANY(%s)",
    "seller_id": "SELECT DISTINCT seller_id FROM retail.sellers WHERE seller_id = ANY(%s)",
    "order_id": "SELECT DISTINCT order_id FROM retail.orders WHERE order_id = ANY(%s)",
}


def _verify_alias_targets(cursor: Any, catalog: CatalogFile) -> None:
    by_domain: dict[str, set[str]] = {}
    for alias in catalog.aliases:
        by_domain.setdefault(alias.domain.value, set()).add(alias.canonical_value)
    for domain, expected in sorted(by_domain.items()):
        cursor.execute(_DOMAIN_QUERIES[domain], (sorted(expected),))
        actual = {str(row[0]) for row in cursor.fetchall()}
        if actual != expected:
            raise KnowledgeCatalogInvalid(
                "alias_target_missing",
                f"catalog aliases reference unavailable values in {domain}",
            )


def _insert_catalog(
    cursor: Any,
    validated: ValidatedCatalog,
    source_sha256: str,
    content_sha256: str,
) -> None:
    catalog = validated.catalog
    cursor.execute(
        "INSERT INTO knowledge.catalog_revision ("
        "revision_id, schema_version, content_sha256, source_path, source_sha256, "
        "data_manifest_sha256, active"
        ") VALUES (%s, %s, %s, %s, %s, %s, false)",
        (
            catalog.revision_id,
            catalog.schema_version,
            content_sha256,
            validated.source_path,
            source_sha256,
            catalog.data_manifest_sha256,
        ),
    )
    cursor.executemany(
        "INSERT INTO knowledge.document ("
        "revision_id, doc_id, kind, title, content, source_type, source_path, "
        "source_sha256, allowed_profiles"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        [
            (
                catalog.revision_id,
                document.doc_id,
                document.kind.value,
                document.title,
                Jsonb(document.content),
                document.source_type,
                document.source_path,
                document.source_sha256,
                list(document.allowed_profiles),
            )
            for document in catalog.documents
        ],
    )
    cursor.executemany(
        "INSERT INTO knowledge.value_alias ("
        "revision_id, domain, normalized_alias, canonical_value, display_label, "
        "locale, source_doc_id"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s)",
        [
            (
                catalog.revision_id,
                alias.domain.value,
                alias.normalized_alias,
                alias.canonical_value,
                alias.display_label,
                alias.locale,
                alias.source_doc_id,
            )
            for alias in catalog.aliases
        ],
    )


def import_catalog(
    admin_dsn: str,
    catalog_path: Path,
    *,
    connect: Callable[..., Any] = psycopg.connect,
) -> ImportOutcome:
    raw_bytes = catalog_path.read_bytes()
    source_sha256 = sha256(raw_bytes).hexdigest()
    validated = load_catalog_bytes(raw_bytes, catalog_path.as_posix())
    catalog = validated.catalog
    content_sha256 = catalog_content_sha256(validated)
    outcome_fields = {
        "revision_id": catalog.revision_id,
        "source_sha256": source_sha256,
        "content_sha256": content_sha256,
        "document_count": len(catalog.documents),
        "alias_count": len(catalog.aliases),
    }

    connection = connect(
        conninfo=admin_dsn,
        application_name="commerce_knowledge_import",
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT revision_id FROM knowledge.catalog_revision "
                "WHERE content_sha256 = %s",
                (content_sha256,),
            )
            if cursor.fetchone() is not None:
                connection.rollback()
                return ImportOutcome(status="no_op", **outcome_fields)

            cursor.execute(
                "SELECT content_sha256 FROM knowledge.catalog_revision "
                "WHERE revision_id = %s",
                (catalog.revision_id,),
            )
            if cursor.fetchone() is not None:
                raise KnowledgeCatalogInvalid(
                    "revision_conflict",
                    "catalog revision ID already exists with different content",
                )

            _verify_alias_targets(cursor, catalog)
            _insert_catalog(cursor, validated, source_sha256, content_sha256)

            cursor.execute(
                "SELECT count(*) FROM knowledge.document WHERE revision_id = %s",
                (catalog.revision_id,),
            )
            if int(cursor.fetchone()[0]) != len(catalog.documents):
                raise KnowledgeCatalogInvalid(
                    "document_count",
                    "inserted document count does not match catalog",
                )
            cursor.execute(
                "SELECT count(*) FROM knowledge.value_alias WHERE revision_id = %s",
                (catalog.revision_id,),
            )
            if int(cursor.fetchone()[0]) != len(catalog.aliases):
                raise KnowledgeCatalogInvalid(
                    "alias_count",
                    "inserted alias count does not match catalog",
                )
            cursor.execute(
                "UPDATE knowledge.catalog_revision SET active = false WHERE active"
            )
            cursor.execute(
                "UPDATE knowledge.catalog_revision SET active = true WHERE revision_id = %s",
                (catalog.revision_id,),
            )
        connection.commit()
        return ImportOutcome(status="inserted", **outcome_fields)
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    outcome = import_catalog(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"],
        repo_root / "data/knowledge/retail_catalog.v1.json",
    )
    print(json.dumps(asdict(outcome), sort_keys=True))


if __name__ == "__main__":
    main()
