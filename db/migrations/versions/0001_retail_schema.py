"""Create the initial retail analysis schema.

Revision ID: 0001_retail_schema
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_retail_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA retail AUTHORIZATION retail_owner")
    op.execute("CREATE SCHEMA ops_read AUTHORIZATION retail_owner")

    op.create_table(
        "customers",
        sa.Column("customer_id", sa.Text(), primary_key=True),
        sa.Column("customer_unique_id", sa.Text(), nullable=False),
        sa.Column("customer_zip_code_prefix", sa.Integer(), nullable=False),
        sa.Column("customer_city", sa.Text(), nullable=False),
        sa.Column("customer_state", sa.String(2), nullable=False),
        schema="retail",
    )
    op.create_table(
        "products",
        sa.Column("product_id", sa.Text(), primary_key=True),
        sa.Column("product_category_name", sa.Text()),
        sa.Column("product_name_lenght", sa.Numeric(10, 1)),
        sa.Column("product_description_lenght", sa.Numeric(10, 1)),
        sa.Column("product_photos_qty", sa.Numeric(10, 1)),
        sa.Column("product_weight_g", sa.Numeric(12, 2)),
        sa.Column("product_length_cm", sa.Numeric(12, 2)),
        sa.Column("product_height_cm", sa.Numeric(12, 2)),
        sa.Column("product_width_cm", sa.Numeric(12, 2)),
        schema="retail",
    )
    op.create_table(
        "sellers",
        sa.Column("seller_id", sa.Text(), primary_key=True),
        sa.Column("seller_zip_code_prefix", sa.Integer(), nullable=False),
        sa.Column("seller_city", sa.Text(), nullable=False),
        sa.Column("seller_state", sa.String(2), nullable=False),
        schema="retail",
    )
    op.create_table(
        "product_category_name_translation",
        sa.Column("product_category_name", sa.Text(), primary_key=True),
        sa.Column("product_category_name_english", sa.Text(), nullable=False),
        schema="retail",
    )
    op.create_table(
        "orders",
        sa.Column("order_id", sa.Text(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Text(),
            sa.ForeignKey("retail.customers.customer_id"),
            nullable=False,
        ),
        sa.Column("order_status", sa.Text(), nullable=False),
        sa.Column("order_purchase_timestamp", sa.DateTime(), nullable=False),
        sa.Column("order_approved_at", sa.DateTime()),
        sa.Column("order_delivered_carrier_date", sa.DateTime()),
        sa.Column("order_delivered_customer_date", sa.DateTime()),
        sa.Column("order_estimated_delivery_date", sa.DateTime(), nullable=False),
        schema="retail",
    )
    op.create_table(
        "order_items",
        sa.Column("order_id", sa.Text(), nullable=False),
        sa.Column("order_item_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("seller_id", sa.Text(), nullable=False),
        sa.Column("shipping_limit_date", sa.DateTime(), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("freight_value", sa.Numeric(12, 2), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["retail.orders.order_id"]),
        sa.ForeignKeyConstraint(["product_id"], ["retail.products.product_id"]),
        sa.ForeignKeyConstraint(["seller_id"], ["retail.sellers.seller_id"]),
        sa.PrimaryKeyConstraint("order_id", "order_item_id"),
        schema="retail",
    )
    op.create_table(
        "order_payments",
        sa.Column("order_id", sa.Text(), nullable=False),
        sa.Column("payment_sequential", sa.Integer(), nullable=False),
        sa.Column("payment_type", sa.Text(), nullable=False),
        sa.Column("payment_installments", sa.Integer(), nullable=False),
        sa.Column("payment_value", sa.Numeric(12, 2), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["retail.orders.order_id"]),
        sa.PrimaryKeyConstraint("order_id", "payment_sequential"),
        schema="retail",
    )
    op.create_table(
        "order_reviews",
        sa.Column("review_row_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("review_id", sa.Text(), nullable=False),
        sa.Column(
            "order_id",
            sa.Text(),
            sa.ForeignKey("retail.orders.order_id"),
            nullable=False,
        ),
        sa.Column("review_score", sa.SmallInteger(), nullable=False),
        sa.Column("review_comment_title", sa.Text()),
        sa.Column("review_comment_message", sa.Text()),
        sa.Column("review_creation_date", sa.DateTime(), nullable=False),
        sa.Column("review_answer_timestamp", sa.DateTime(), nullable=False),
        schema="retail",
    )

    op.create_index("ix_orders_customer_id", "orders", ["customer_id"], schema="retail")
    op.create_index(
        "ix_orders_purchase_timestamp",
        "orders",
        ["order_purchase_timestamp"],
        schema="retail",
    )
    op.create_index("ix_order_items_product_id", "order_items", ["product_id"], schema="retail")
    op.create_index("ix_order_items_seller_id", "order_items", ["seller_id"], schema="retail")
    op.create_index("ix_order_reviews_review_id", "order_reviews", ["review_id"], schema="retail")
    op.create_index(
        "ix_products_category_name",
        "products",
        ["product_category_name"],
        schema="retail",
    )

    for table_name in (
        "customers",
        "orders",
        "order_items",
        "order_payments",
        "order_reviews",
        "products",
        "sellers",
        "product_category_name_translation",
    ):
        op.execute(f"ALTER TABLE retail.{table_name} OWNER TO retail_owner")
    op.execute("GRANT USAGE ON SCHEMA retail, ops_read TO agent_reader")
    op.execute(
        "GRANT SELECT ON retail.customers, retail.orders, retail.order_items, "
        "retail.order_payments, retail.order_reviews, retail.products, "
        "retail.sellers, retail.product_category_name_translation TO agent_reader"
    )
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA retail FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA retail FROM agent_reader")


def downgrade() -> None:
    op.execute("DROP SCHEMA ops_read CASCADE")
    op.execute("DROP SCHEMA retail CASCADE")
