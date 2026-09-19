"""Create isolated checkpoint and provider-private runtime-state schemas.

Revision ID: 0003_day3_runtime_state
Revises: 0002_knowledge_baseline
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_day3_runtime_state"
down_revision: str | None = "0002_knowledge_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA checkpoint AUTHORIZATION checkpoint_owner")
    op.execute("CREATE SCHEMA model_state AUTHORIZATION model_state_owner")

    op.create_table(
        "provider_turn",
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_digest", sa.Text(), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column(
            "expected_tool_call_ids",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
        ),
        sa.Column("token_weight", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("turn_id", name="pk_provider_turn"),
        sa.UniqueConstraint(
            "scope_digest",
            "attempt_id",
            "sequence",
            name="uq_provider_turn_scope_attempt_sequence",
        ),
        sa.CheckConstraint(
            "scope_digest ~ '^[0-9a-f]{64}$'",
            name="ck_provider_turn_scope_digest",
        ),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_provider_turn_payload_sha256",
        ),
        sa.CheckConstraint("sequence >= 0", name="ck_provider_turn_sequence"),
        sa.CheckConstraint("token_weight >= 0", name="ck_provider_turn_token_weight"),
        sa.CheckConstraint(
            "length(provider) > 0 AND length(model) > 0",
            name="ck_provider_turn_provider_model_nonempty",
        ),
        sa.CheckConstraint(
            "last_used_at >= created_at",
            name="ck_provider_turn_last_used_at",
        ),
        sa.CheckConstraint(
            "absolute_expires_at = created_at + interval '7 days'",
            name="ck_provider_turn_absolute_expiry",
        ),
        sa.CheckConstraint(
            "(terminal_at IS NULL AND payload IS NOT NULL) OR "
            "(terminal_at IS NOT NULL AND terminal_at >= created_at AND payload IS NULL)",
            name="ck_provider_turn_terminal_payload",
        ),
        schema="model_state",
    )
    op.execute("ALTER TABLE model_state.provider_turn OWNER TO model_state_owner")

    op.execute("REVOKE ALL ON SCHEMA checkpoint FROM PUBLIC")
    op.execute("REVOKE ALL ON SCHEMA model_state FROM PUBLIC")
    op.execute("REVOKE ALL ON TABLE model_state.provider_turn FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA model_state TO model_state_writer")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
        "model_state.provider_turn TO model_state_writer"
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA model_state CASCADE")
    op.execute("DROP SCHEMA checkpoint CASCADE")
