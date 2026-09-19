"""Create the versioned Knowledge catalog and safe reader projections.

Revision ID: 0002_knowledge_baseline
Revises: 0001_retail_schema
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_knowledge_baseline"
down_revision: str | None = "0001_retail_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_KNOWLEDGE_KINDS = "'table','column','join','metric','data_quality','permission'"
_VALUE_DOMAINS = (
    "'customer_state','seller_state','order_status','payment_type',"
    "'product_category','customer_city','seller_city','seller_id','order_id'"
)


def upgrade() -> None:
    op.execute("CREATE SCHEMA knowledge AUTHORIZATION knowledge_owner")

    op.create_table(
        "catalog_revision",
        sa.Column("revision_id", sa.Text(), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False, unique=True),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.Text(), nullable=False),
        sa.Column("data_manifest_sha256", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("schema_version > 0", name="ck_catalog_revision_schema_version"),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_catalog_revision_content_sha256",
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_catalog_revision_source_sha256",
        ),
        sa.CheckConstraint(
            "data_manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_catalog_revision_data_manifest_sha256",
        ),
        schema="knowledge",
    )
    op.create_index(
        "uq_catalog_revision_single_active",
        "catalog_revision",
        ["active"],
        unique=True,
        schema="knowledge",
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "document",
        sa.Column("revision_id", sa.Text(), nullable=False),
        sa.Column("doc_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.Text(), nullable=False),
        sa.Column("allowed_profiles", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.CheckConstraint(f"kind IN ({_KNOWLEDGE_KINDS})", name="ck_document_kind"),
        sa.CheckConstraint("length(title) > 0", name="ck_document_title"),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_document_source_sha256",
        ),
        sa.CheckConstraint(
            "cardinality(allowed_profiles) > 0",
            name="ck_document_allowed_profiles_nonempty",
        ),
        sa.CheckConstraint(
            "allowed_profiles <@ ARRAY['retail']::text[]",
            name="ck_document_allowed_profiles_reviewed",
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["knowledge.catalog_revision.revision_id"],
        ),
        sa.PrimaryKeyConstraint("revision_id", "doc_id"),
        schema="knowledge",
    )

    op.create_table(
        "value_alias",
        sa.Column("revision_id", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.Column("canonical_value", sa.Text(), nullable=False),
        sa.Column("display_label", sa.Text(), nullable=False),
        sa.Column("locale", sa.Text(), nullable=False),
        sa.Column("source_doc_id", sa.Text(), nullable=False),
        sa.CheckConstraint(f"domain IN ({_VALUE_DOMAINS})", name="ck_value_alias_domain"),
        sa.CheckConstraint(
            "locale IN ('en','pt-BR','zh-CN')",
            name="ck_value_alias_locale",
        ),
        sa.CheckConstraint(
            "length(normalized_alias) > 0 AND length(canonical_value) > 0",
            name="ck_value_alias_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["knowledge.catalog_revision.revision_id"],
        ),
        sa.ForeignKeyConstraint(
            ["revision_id", "source_doc_id"],
            ["knowledge.document.revision_id", "knowledge.document.doc_id"],
        ),
        sa.PrimaryKeyConstraint("revision_id", "domain", "normalized_alias"),
        schema="knowledge",
    )

    for table_name in ("catalog_revision", "document", "value_alias"):
        op.execute(f"ALTER TABLE knowledge.{table_name} OWNER TO knowledge_owner")

    op.execute(
        "CREATE VIEW knowledge.retail_documents WITH (security_barrier=true) AS "
        "SELECT r.revision_id AS revision, r.content_sha256 AS catalog_sha256, "
        "d.doc_id, d.kind, d.title, d.content, d.source_type, d.source_path, "
        "d.source_sha256 "
        "FROM knowledge.catalog_revision AS r "
        "JOIN knowledge.document AS d ON d.revision_id = r.revision_id "
        "WHERE r.active AND 'retail' = ANY(d.allowed_profiles)"
    )
    op.execute("ALTER VIEW knowledge.retail_documents OWNER TO knowledge_owner")

    op.execute("GRANT USAGE ON SCHEMA knowledge TO retail_owner")
    op.execute(
        "GRANT SELECT ON knowledge.catalog_revision, knowledge.value_alias TO retail_owner"
    )
    op.execute(
        "CREATE VIEW ops_read.business_value_aliases WITH (security_barrier=true) AS "
        "SELECT r.revision_id AS revision, r.content_sha256 AS catalog_sha256, "
        "r.data_manifest_sha256, a.domain, a.normalized_alias, a.canonical_value, "
        "a.display_label, a.locale, a.source_doc_id "
        "FROM knowledge.catalog_revision AS r "
        "JOIN knowledge.value_alias AS a ON a.revision_id = r.revision_id "
        "WHERE r.active"
    )
    op.execute("ALTER VIEW ops_read.business_value_aliases OWNER TO retail_owner")

    op.execute("REVOKE ALL ON SCHEMA knowledge FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA knowledge FROM PUBLIC")
    op.execute("REVOKE ALL ON knowledge.retail_documents FROM PUBLIC")
    op.execute("REVOKE ALL ON ops_read.business_value_aliases FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA knowledge TO knowledge_reader")
    op.execute("GRANT SELECT ON knowledge.retail_documents TO knowledge_reader")
    op.execute("GRANT SELECT ON ops_read.business_value_aliases TO agent_reader")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS ops_read.business_value_aliases")
    op.execute("DROP VIEW IF EXISTS knowledge.retail_documents")
    op.execute("DROP SCHEMA knowledge CASCADE")
