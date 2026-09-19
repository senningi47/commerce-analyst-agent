"""Build the deterministic reviewed retail Knowledge catalog."""

import csv
import json
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path

from commerce_agent.knowledge._catalog import (
    CatalogAlias,
    CatalogDocument,
    CatalogFile,
    CatalogValueDomain,
    canonical_catalog_bytes,
    load_catalog_bytes,
)
from commerce_agent.knowledge.contracts import KnowledgeKind
from commerce_agent.query_engine._ast_policy import ALLOWED_JOIN_EDGES, ALLOWED_TABLES
from commerce_agent.value_resolver._matching import normalize_text

MANIFEST_RELATIVE_PATH = Path(
    "data/manifests/olistbr-brazilian-ecommerce-v2-manifest.json"
)
DATASET_RELATIVE_PATH = Path("data/raw/olistbr-brazilian-ecommerce-v2/files")
CATALOG_RELATIVE_PATH = Path("data/knowledge/retail_catalog.v1.json")
EXPECTED_MANIFEST_SHA256 = "0ef8f69b909bbcaefce0a9f644ddf858b0997abd949f2bed7272f8373a694c24"
OLIST_FILES = (
    "olist_customers_dataset.csv",
    "olist_products_dataset.csv",
    "olist_sellers_dataset.csv",
    "product_category_name_translation.csv",
    "olist_orders_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_order_reviews_dataset.csv",
)


STATE_NAMES = {
    "AC": "Acre",
    "AL": "Alagoas",
    "AP": "Amapá",
    "AM": "Amazonas",
    "BA": "Bahia",
    "CE": "Ceará",
    "DF": "Distrito Federal",
    "ES": "Espírito Santo",
    "GO": "Goiás",
    "MA": "Maranhão",
    "MT": "Mato Grosso",
    "MS": "Mato Grosso do Sul",
    "MG": "Minas Gerais",
    "PA": "Pará",
    "PB": "Paraíba",
    "PR": "Paraná",
    "PE": "Pernambuco",
    "PI": "Piauí",
    "RJ": "Rio de Janeiro",
    "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul",
    "RO": "Rondônia",
    "RR": "Roraima",
    "SC": "Santa Catarina",
    "SP": "São Paulo",
    "SE": "Sergipe",
    "TO": "Tocantins",
}

STATUS_ALIASES = {
    "approved": (("aprovado", "pt-BR"), ("已批准", "zh-CN")),
    "canceled": (("cancelado", "pt-BR"), ("已取消", "zh-CN")),
    "created": (("criado", "pt-BR"), ("已创建", "zh-CN")),
    "delivered": (("entregue", "pt-BR"), ("已送达", "zh-CN")),
    "invoiced": (("faturado", "pt-BR"), ("已开票", "zh-CN")),
    "processing": (("em processamento", "pt-BR"), ("处理中", "zh-CN")),
    "shipped": (("enviado", "pt-BR"), ("已发货", "zh-CN")),
    "unavailable": (("indisponível", "pt-BR"), ("缺货", "zh-CN")),
}

PAYMENT_ALIASES = {
    "boleto": (("boleto bancário", "pt-BR"), ("银行票据", "zh-CN")),
    "credit_card": (("cartão de crédito", "pt-BR"), ("信用卡", "zh-CN")),
    "debit_card": (("cartão de débito", "pt-BR"), ("借记卡", "zh-CN")),
    "not_defined": (("não definido", "pt-BR"), ("未定义", "zh-CN")),
    "voucher": (("vale", "pt-BR"), ("代金券", "zh-CN")),
}


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_verified_manifest(repo_root: Path) -> tuple[dict[str, object], str]:
    manifest_path = repo_root / MANIFEST_RELATIVE_PATH
    manifest_sha256 = _sha256_file(manifest_path)
    if manifest_sha256 != EXPECTED_MANIFEST_SHA256:
        raise RuntimeError("Olist manifest SHA-256 mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_files = {entry["name"]: entry for entry in manifest["files"]}
    dataset_dir = repo_root / DATASET_RELATIVE_PATH
    for file_name in OLIST_FILES:
        source = dataset_dir / file_name
        if _sha256_file(source) != manifest_files[file_name]["sha256"]:
            raise RuntimeError(f"Olist source SHA-256 mismatch: {file_name}")
    return manifest, manifest_sha256


def _distinct_column(path: Path, column: str) -> set[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return {row[column] for row in csv.DictReader(source)}


def _document(
    doc_id: str,
    kind: KnowledgeKind,
    title: str,
    content: dict[str, object],
    source_type: str,
    source_path: Path,
    source_sha256: str,
) -> CatalogDocument:
    return CatalogDocument(
        doc_id=doc_id,
        kind=kind,
        title=title,
        content=content,
        source_type=source_type,
        source_path=source_path.as_posix(),
        source_sha256=source_sha256,
        allowed_profiles=("retail",),
    )


def _table_documents(repo_root: Path) -> list[CatalogDocument]:
    source_path = Path("db/migrations/versions/0001_retail_schema.py")
    source_sha256 = _sha256_file(repo_root / source_path)
    return [
        _document(
            f"table.{table_name}",
            KnowledgeKind.TABLE,
            f"retail.{table_name}",
            {"schema": "retail", "columns": sorted(columns)},
            "migration",
            source_path,
            source_sha256,
        )
        for table_name, columns in sorted(ALLOWED_TABLES.items())
    ]


def _join_documents(repo_root: Path) -> list[CatalogDocument]:
    source_path = Path("src/commerce_agent/query_engine/_ast_policy.py")
    source_sha256 = _sha256_file(repo_root / source_path)
    edges = sorted(tuple(sorted(edge)) for edge in ALLOWED_JOIN_EDGES)
    return [
        _document(
            "join." + "__".join(f"{table}_{column}" for table, column in edge),
            KnowledgeKind.JOIN,
            " = ".join(f"retail.{table}.{column}" for table, column in edge),
            {
                "left": {"table": edge[0][0], "column": edge[0][1]},
                "right": {"table": edge[1][0], "column": edge[1][1]},
                "operator": "=",
            },
            "ast_policy",
            source_path,
            source_sha256,
        )
        for edge in edges
    ]


def _quality_documents(
    manifest: dict[str, object],
    manifest_sha256: str,
) -> list[CatalogDocument]:
    source_path = MANIFEST_RELATIVE_PATH
    money = manifest["monetary_reconciliation_cents"]
    return [
        _document(
            "data_quality.review_id_non_unique",
            KnowledgeKind.DATA_QUALITY,
            "review_id is not unique",
            {"rows": 99_224, "distinct_review_ids": 98_410},
            "data_manifest",
            source_path,
            manifest_sha256,
        ),
        _document(
            "data_quality.category_translation_gaps",
            KnowledgeKind.DATA_QUALITY,
            "Product category translation gaps",
            {"missing_rows": 13},
            "data_manifest",
            source_path,
            manifest_sha256,
        ),
        _document(
            "data_quality.review_whitespace_null",
            KnowledgeKind.DATA_QUALITY,
            "Blank review text is normalized to NULL",
            {"columns": ["review_comment_title", "review_comment_message"]},
            "data_manifest",
            source_path,
            manifest_sha256,
        ),
        _document(
            "data_quality.monetary_mismatch",
            KnowledgeKind.DATA_QUALITY,
            "Payments differ from item plus freight totals",
            {
                "item_total_cents": money["item_total"],
                "payment_total_cents": money["payment_value"],
                "difference_cents": money["payment_minus_item_total"],
            },
            "data_manifest",
            source_path,
            manifest_sha256,
        ),
    ]


def _permission_documents(repo_root: Path) -> list[CatalogDocument]:
    source_path = Path("db/migrations/versions/0002_knowledge_baseline.py")
    source_sha256 = _sha256_file(repo_root / source_path)
    return [
        _document(
            "permission.agent_reader",
            KnowledgeKind.PERMISSION,
            "agent_reader boundary",
            {"allowed": ["retail tables", "business value alias view"], "write": False},
            "security_migration",
            source_path,
            source_sha256,
        ),
        _document(
            "permission.knowledge_reader",
            KnowledgeKind.PERMISSION,
            "knowledge_reader boundary",
            {"allowed": ["retail Knowledge view"], "retail_access": False, "write": False},
            "security_migration",
            source_path,
            source_sha256,
        ),
    ]


def _metric_documents(repo_root: Path) -> list[CatalogDocument]:
    source_path = Path(
        "docs/project/specs/2026-08-31-day-2b-business-value-knowledge-design.md"
    )
    source_sha256 = _sha256_file(repo_root / source_path)
    definitions = {
        "order_count": {
            "status": "active",
            "definition": "COUNT(DISTINCT orders.order_id)",
            "implicit_status_filter": False,
        },
        "item_amount": {
            "status": "active",
            "definition": "SUM(order_items.price)",
            "currency": "BRL",
            "grain": "order_item",
            "includes_freight": False,
        },
        "freight_amount": {
            "status": "active",
            "definition": "SUM(order_items.freight_value)",
            "currency": "BRL",
            "grain": "order_item",
        },
        "payment_amount": {
            "status": "active",
            "definition": "SUM(order_payments.payment_value)",
            "currency": "BRL",
            "grain": "payment_record",
        },
        "gmv": {
            "status": "clarification_required",
            "required_clarifications": [
                "fact source",
                "freight inclusion",
                "order status set",
                "time field",
            ],
        },
    }
    return [
        _document(
            f"metric.{name}",
            KnowledgeKind.METRIC,
            name,
            content,
            "design_spec",
            source_path,
            source_sha256,
        )
        for name, content in definitions.items()
    ]


def _category_aliases(dataset_dir: Path) -> list[CatalogAlias]:
    path = dataset_dir / "product_category_name_translation.csv"
    aliases: list[CatalogAlias] = []
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            aliases.append(
                CatalogAlias(
                    domain=CatalogValueDomain.PRODUCT_CATEGORY,
                    normalized_alias=normalize_text(row["product_category_name_english"]),
                    canonical_value=row["product_category_name"],
                    display_label=row["product_category_name"],
                    locale="en",
                    source_doc_id="table.product_category_name_translation",
                )
            )
    return aliases


def _state_aliases(dataset_dir: Path) -> list[CatalogAlias]:
    sources = (
        (
            CatalogValueDomain.CUSTOMER_STATE,
            dataset_dir / "olist_customers_dataset.csv",
            "customer_state",
            "table.customers",
        ),
        (
            CatalogValueDomain.SELLER_STATE,
            dataset_dir / "olist_sellers_dataset.csv",
            "seller_state",
            "table.sellers",
        ),
    )
    aliases: list[CatalogAlias] = []
    for domain, path, column, source_doc_id in sources:
        for state in sorted(_distinct_column(path, column)):
            aliases.append(
                CatalogAlias(
                    domain=domain,
                    normalized_alias=normalize_text(STATE_NAMES[state]),
                    canonical_value=state,
                    display_label=STATE_NAMES[state],
                    locale="pt-BR",
                    source_doc_id=source_doc_id,
                )
            )
    return aliases


def _reviewed_aliases(
    domain: CatalogValueDomain,
    mappings: dict[str, tuple[tuple[str, str], ...]],
    source_doc_id: str,
) -> Iterable[CatalogAlias]:
    for canonical_value, values in mappings.items():
        for alias, locale in values:
            yield CatalogAlias(
                domain=domain,
                normalized_alias=normalize_text(alias),
                canonical_value=canonical_value,
                display_label=alias,
                locale=locale,
                source_doc_id=source_doc_id,
            )


def build_catalog(repo_root: Path) -> CatalogFile:
    repo_root = repo_root.resolve()
    manifest, manifest_sha256 = _load_verified_manifest(repo_root)
    dataset_dir = repo_root / DATASET_RELATIVE_PATH

    actual_statuses = _distinct_column(dataset_dir / "olist_orders_dataset.csv", "order_status")
    if actual_statuses != set(STATUS_ALIASES):
        raise RuntimeError("reviewed order statuses do not match Olist source")
    actual_payments = _distinct_column(
        dataset_dir / "olist_order_payments_dataset.csv",
        "payment_type",
    )
    if actual_payments != set(PAYMENT_ALIASES):
        raise RuntimeError("reviewed payment types do not match Olist source")

    documents = (
        _table_documents(repo_root)
        + _join_documents(repo_root)
        + _quality_documents(manifest, manifest_sha256)
        + _permission_documents(repo_root)
        + _metric_documents(repo_root)
    )
    aliases = (
        _category_aliases(dataset_dir)
        + _state_aliases(dataset_dir)
        + list(
            _reviewed_aliases(
                CatalogValueDomain.ORDER_STATUS,
                STATUS_ALIASES,
                "table.orders",
            )
        )
        + list(
            _reviewed_aliases(
                CatalogValueDomain.PAYMENT_TYPE,
                PAYMENT_ALIASES,
                "table.order_payments",
            )
        )
    )
    candidate = CatalogFile(
        schema_version=1,
        revision_id="retail-catalog-v1",
        data_manifest_sha256=manifest_sha256,
        documents=tuple(sorted(documents, key=lambda item: item.doc_id)),
        aliases=tuple(
            sorted(aliases, key=lambda item: (item.domain.value, item.normalized_alias))
        ),
    )
    encoded = json.dumps(
        candidate.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return load_catalog_bytes(encoded, CATALOG_RELATIVE_PATH.as_posix()).catalog


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    catalog = build_catalog(repo_root)
    validated = load_catalog_bytes(
        json.dumps(catalog.model_dump(mode="json"), ensure_ascii=False).encode("utf-8"),
        CATALOG_RELATIVE_PATH.as_posix(),
    )
    output_path = repo_root / CATALOG_RELATIVE_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pretty = json.dumps(
        validated.catalog.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    output_path.write_text(pretty + "\n", encoding="utf-8", newline="\n")
    print(
        "Knowledge catalog built: "
        f"documents={len(catalog.documents)} aliases={len(catalog.aliases)} "
        f"canonical_bytes={len(canonical_catalog_bytes(validated))}"
    )


if __name__ == "__main__":
    main()
