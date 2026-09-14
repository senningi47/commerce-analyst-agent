"""Expose the Product Trace as one whitelisted ops_read view for the SSE API.

Revision ID: 0006_day6_trace_read_view
Revises: 0005_day5_evaluation_schema

Day 6 Task 7: the SSE event surface reads the app-track audit trail, but no
role may SELECT the raw ``app.product_trace_event`` table (Day 4 froze that
ACL: append-only trace_writer, scenario-scoped reset only). The view is the
payload whitelist itself - it exposes exactly the public columns and nothing
else - and grants read access to the existing ``agent_reader`` identity.
Base-table grants stay untouched. Revision ids stay <= 32 chars: alembic's
version_num column is varchar(32) and a longer id fails the version bump.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_day6_trace_read_view"
down_revision: str | None = "0005_day5_evaluation_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIEW_COLUMNS = (
    "run_id, attempt_id, sequence, event_type, status, safe_summary, "
    "reason_code, occurred_at"
)


def upgrade() -> None:
    op.execute(
        "CREATE VIEW ops_read.product_trace_events WITH (security_barrier=true) AS\n"
        f"SELECT {_VIEW_COLUMNS}\n"
        "FROM app.product_trace_event"
    )
    op.execute("ALTER VIEW ops_read.product_trace_events OWNER TO ops_owner")
    op.execute("REVOKE ALL ON ops_read.product_trace_events FROM PUBLIC")
    op.execute("GRANT SELECT ON ops_read.product_trace_events TO agent_reader")


def downgrade() -> None:
    op.execute("DROP VIEW ops_read.product_trace_events")
