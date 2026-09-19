"""Validation and canonical hashing for checked-in Knowledge catalogs."""

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, JsonValue, ValidationError

from commerce_agent.knowledge.contracts import KnowledgeKind
from commerce_agent.knowledge.errors import KnowledgeCatalogInvalid


class CatalogValueDomain(StrEnum):
    CUSTOMER_STATE = "customer_state"
    SELLER_STATE = "seller_state"
    ORDER_STATUS = "order_status"
    PAYMENT_TYPE = "payment_type"
    PRODUCT_CATEGORY = "product_category"
    CUSTOMER_CITY = "customer_city"
    SELLER_CITY = "seller_city"
    SELLER_ID = "seller_id"
    ORDER_ID = "order_id"


class CatalogDocument(BaseModel, frozen=True):
    doc_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")
    kind: KnowledgeKind
    title: str = Field(min_length=1, max_length=200)
    content: dict[str, JsonValue]
    source_type: str = Field(min_length=1, max_length=64)
    source_path: str = Field(min_length=1, max_length=500)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    allowed_profiles: tuple[Literal["retail"], ...] = Field(min_length=1)


class CatalogAlias(BaseModel, frozen=True):
    domain: CatalogValueDomain
    normalized_alias: str = Field(min_length=1, max_length=256)
    canonical_value: str = Field(min_length=1, max_length=256)
    display_label: str = Field(min_length=1, max_length=256)
    locale: Literal["en", "pt-BR", "zh-CN"]
    source_doc_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")


class CatalogFile(BaseModel, frozen=True):
    schema_version: Literal[1]
    revision_id: str = Field(pattern=r"^retail-catalog-v[0-9]+$")
    data_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    documents: tuple[CatalogDocument, ...] = Field(min_length=1)
    aliases: tuple[CatalogAlias, ...]


@dataclass(frozen=True)
class ValidatedCatalog:
    catalog: CatalogFile
    source_path: str


def load_catalog_bytes(data: bytes, source_path: str) -> ValidatedCatalog:
    try:
        payload = json.loads(data.decode("utf-8"))
        catalog = CatalogFile.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError) as error:
        raise KnowledgeCatalogInvalid(
            "catalog_shape",
            "catalog does not match the reviewed schema",
        ) from error

    _validate_invariants(catalog)
    return ValidatedCatalog(catalog=catalog, source_path=source_path)


def _validate_invariants(catalog: CatalogFile) -> None:
    doc_ids = [document.doc_id for document in catalog.documents]
    if len(doc_ids) != len(set(doc_ids)):
        raise KnowledgeCatalogInvalid("duplicate_doc_id", "catalog document IDs must be unique")

    alias_keys = [(alias.domain, alias.normalized_alias) for alias in catalog.aliases]
    if len(alias_keys) != len(set(alias_keys)):
        raise KnowledgeCatalogInvalid(
            "duplicate_alias",
            "catalog alias keys must be unique within a revision",
        )

    known_doc_ids = set(doc_ids)
    if any(alias.source_doc_id not in known_doc_ids for alias in catalog.aliases):
        raise KnowledgeCatalogInvalid(
            "alias_source_missing",
            "catalog alias references a missing source document",
        )

    gmv_documents = [document for document in catalog.documents if document.doc_id == "metric.gmv"]
    if not gmv_documents:
        raise KnowledgeCatalogInvalid("gmv_missing", "catalog must define metric.gmv")
    if len(gmv_documents) != 1 or gmv_documents[0].content.get("status") != (
        "clarification_required"
    ):
        raise KnowledgeCatalogInvalid(
            "gmv_definition",
            "metric.gmv must require clarification",
        )


def canonical_catalog_bytes(catalog: ValidatedCatalog | CatalogFile) -> bytes:
    catalog_file = catalog.catalog if isinstance(catalog, ValidatedCatalog) else catalog
    payload = catalog_file.model_dump(mode="json")
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def catalog_content_sha256(catalog: ValidatedCatalog | CatalogFile) -> str:
    return hashlib.sha256(canonical_catalog_bytes(catalog)).hexdigest()
