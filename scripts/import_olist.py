"""Import the eight approved Olist business files into the product database."""

import argparse
import json
import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from pydantic import BaseModel


@dataclass(frozen=True)
class ImportSpec:
    file_name: str
    table_name: str
    columns: tuple[str, ...]


IMPORT_SPECS = (
    ImportSpec(
        "olist_customers_dataset.csv",
        "customers",
        (
            "customer_id",
            "customer_unique_id",
            "customer_zip_code_prefix",
            "customer_city",
            "customer_state",
        ),
    ),
    ImportSpec(
        "olist_products_dataset.csv",
        "products",
        (
            "product_id",
            "product_category_name",
            "product_name_lenght",
            "product_description_lenght",
            "product_photos_qty",
            "product_weight_g",
            "product_length_cm",
            "product_height_cm",
            "product_width_cm",
        ),
    ),
    ImportSpec(
        "olist_sellers_dataset.csv",
        "sellers",
        (
            "seller_id",
            "seller_zip_code_prefix",
            "seller_city",
            "seller_state",
        ),
    ),
    ImportSpec(
        "product_category_name_translation.csv",
        "product_category_name_translation",
        (
            "product_category_name",
            "product_category_name_english",
        ),
    ),
    ImportSpec(
        "olist_orders_dataset.csv",
        "orders",
        (
            "order_id",
            "customer_id",
            "order_status",
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ),
    ),
    ImportSpec(
        "olist_order_items_dataset.csv",
        "order_items",
        (
            "order_id",
            "order_item_id",
            "product_id",
            "seller_id",
            "shipping_limit_date",
            "price",
            "freight_value",
        ),
    ),
    ImportSpec(
        "olist_order_payments_dataset.csv",
        "order_payments",
        (
            "order_id",
            "payment_sequential",
            "payment_type",
            "payment_installments",
            "payment_value",
        ),
    ),
    ImportSpec(
        "olist_order_reviews_dataset.csv",
        "order_reviews",
        (
            "review_id",
            "order_id",
            "review_score",
            "review_comment_title",
            "review_comment_message",
            "review_creation_date",
            "review_answer_timestamp",
        ),
    ),
)


class ImportReport(BaseModel, frozen=True):
    table_rows: dict[str, int]
    review_distinct_ids: int
    missing_category_translations: int
    item_total_cents: int
    payment_total_cents: int
    payment_minus_item_total_cents: int


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_empty_targets(table_rows: dict[str, int]) -> None:
    non_empty = {name: count for name, count in table_rows.items() if count != 0}
    if non_empty:
        names = ", ".join(sorted(non_empty))
        raise RuntimeError(f"refusing to overwrite non-empty tables: {names}")


def _manifest_files(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["name"]: entry for entry in manifest["files"]}


def _table_counts(cursor: psycopg.Cursor[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for spec in IMPORT_SPECS:
        cursor.execute(
            sql.SQL("SELECT count(*) FROM retail.{}").format(sql.Identifier(spec.table_name))
        )
        counts[spec.table_name] = int(cursor.fetchone()[0])
    return counts


def _copy_file(cursor: psycopg.Cursor[Any], dataset_dir: Path, spec: ImportSpec) -> None:
    statement = sql.SQL("COPY retail.{} ({}) FROM STDIN WITH (FORMAT CSV, HEADER TRUE)").format(
        sql.Identifier(spec.table_name),
        sql.SQL(", ").join(sql.Identifier(column) for column in spec.columns),
    )
    with cursor.copy(statement) as copy, (dataset_dir / spec.file_name).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            copy.write(chunk)


def _normalize_review_blanks(cursor: psycopg.Cursor[Any]) -> None:
    cursor.execute(
        "UPDATE retail.order_reviews SET "
        "review_comment_title = CASE WHEN review_comment_title ~ '^[[:space:]]*$' "
        "THEN NULL ELSE review_comment_title END, "
        "review_comment_message = CASE WHEN review_comment_message ~ '^[[:space:]]*$' "
        "THEN NULL ELSE review_comment_message END "
        "WHERE review_comment_title ~ '^[[:space:]]*$' "
        "OR review_comment_message ~ '^[[:space:]]*$'"
    )


def import_olist(admin_dsn: str, dataset_dir: Path, manifest_path: Path) -> ImportReport:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive_path = manifest_path.parents[2] / manifest["archive"]["relative_path"]
    if sha256_file(archive_path) != manifest["archive"]["sha256"]:
        raise RuntimeError("Olist archive SHA-256 mismatch")

    manifest_files = _manifest_files(manifest)
    for spec in IMPORT_SPECS:
        source = dataset_dir / spec.file_name
        expected = manifest_files[spec.file_name]
        if sha256_file(source) != expected["sha256"]:
            raise RuntimeError(f"SHA-256 mismatch: {spec.file_name}")

    with (
        psycopg.connect(admin_dsn) as connection,
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        verify_empty_targets(_table_counts(cursor))
        for spec in IMPORT_SPECS:
            _copy_file(cursor, dataset_dir, spec)
        _normalize_review_blanks(cursor)

        rows = _table_counts(cursor)
        for spec in IMPORT_SPECS:
            expected_rows = int(manifest_files[spec.file_name]["row_count"])
            if rows[spec.table_name] != expected_rows:
                raise RuntimeError(f"row-count mismatch: {spec.table_name}")

            expected_nulls = manifest_files[spec.file_name]["null_counts"]
            for column_name, expected_count in expected_nulls.items():
                cursor.execute(
                    sql.SQL("SELECT count(*) FROM retail.{} WHERE {} IS NULL").format(
                        sql.Identifier(spec.table_name),
                        sql.Identifier(column_name),
                    )
                )
                if int(cursor.fetchone()[0]) != int(expected_count):
                    raise RuntimeError(f"NULL-count mismatch: {spec.table_name}.{column_name}")

        cursor.execute("SELECT count(DISTINCT review_id) FROM retail.order_reviews")
        review_distinct_ids = int(cursor.fetchone()[0])
        review_key_fact = next(
            entry
            for entry in manifest["primary_key_candidates"]
            if entry["table"] == "reviews" and entry["columns"] == ["review_id"]
        )
        if review_distinct_ids != int(review_key_fact["distinct_key_count"]):
            raise RuntimeError("review_id distinct-count mismatch")

        cursor.execute(
            "SELECT count(*) FROM retail.products AS p "
            "WHERE p.product_category_name IS NOT NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM retail.product_category_name_translation AS t "
            "WHERE t.product_category_name = p.product_category_name)"
        )
        missing_category_translations = int(cursor.fetchone()[0])
        translation_fact = next(
            entry
            for entry in manifest["foreign_key_checks"]
            if entry["reference"].startswith("products.product_category_name")
        )
        if missing_category_translations != int(translation_fact["missing_rows"]):
            raise RuntimeError("category translation gap mismatch")

        cursor.execute(
            "SELECT round((sum(price) + sum(freight_value)) * 100)::bigint FROM retail.order_items"
        )
        item_total_cents = int(cursor.fetchone()[0])
        cursor.execute("SELECT round(sum(payment_value) * 100)::bigint FROM retail.order_payments")
        payment_total_cents = int(cursor.fetchone()[0])
        expected_money = manifest["monetary_reconciliation_cents"]
        if item_total_cents != int(expected_money["item_total"]):
            raise RuntimeError("item monetary reconciliation mismatch")
        if payment_total_cents != int(expected_money["payment_value"]):
            raise RuntimeError("payment monetary reconciliation mismatch")

        for spec in IMPORT_SPECS:
            cursor.execute(sql.SQL("ANALYZE retail.{}").format(sql.Identifier(spec.table_name)))

    return ImportReport(
        table_rows=rows,
        review_distinct_ids=review_distinct_ids,
        missing_category_translations=missing_category_translations,
        item_total_cents=item_total_cents,
        payment_total_cents=payment_total_cents,
        payment_minus_item_total_cents=payment_total_cents - item_total_cents,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    arguments = parser.parse_args()

    report = import_olist(
        admin_dsn=os.environ["PRODUCT_POSTGRES_ADMIN_DSN"],
        dataset_dir=arguments.dataset_dir,
        manifest_path=arguments.manifest,
    )
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
