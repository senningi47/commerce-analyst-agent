"""Expose the eval schema as whitelisted ops_read views for the read-only API.

Revision ID: 0007_day6_eval_read_view
Revises: 0006_day6_trace_read_view

Day 6 Task 8 Step 3: the eval center reads real experiment data, but no
role may SELECT the raw ``eval`` tables beyond the evaluation_writer/owner
pair (Day 5 froze that ACL). Following the 0006 pattern, the views are the
whitelist itself: they expose exactly the public columns - identity, mode,
status, evaluator result readings, and the agent-side cost/turn telemetry
keys - and nothing else (no GT-adjacent fields, no spool paths, no session
ids). The views are owned by ``evaluation_owner`` so the underlying
permission checks keep running as the eval owner and the base-table grants
stay untouched. Read access goes to the existing ``agent_reader`` identity
(``PRODUCT_DATABASE_DSN``). Revision ids stay <= 32 chars (alembic's
version_num is varchar(32)).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_day6_eval_read_view"
down_revision: str | None = "0006_day6_trace_read_view"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXPERIMENT_COLUMNS = (
    "experiment_id",
    "purpose",
    "config_hash",
    "created_at",
    "closed_at",
)

_ATTEMPT_COLUMNS = (
    "a.attempt_id",
    "a.run_id",
    "a.experiment_id",
    "a.task_id",
    "a.mode",
    "a.attempt_seq",
    "a.status",
    "a.error_class",
    "a.started_at",
    "a.finished_at",
    "r.reward",
    "r.phase1_passed",
    "r.phase2_passed",
    "r.rounds",
    "r.tool_calls",
    "r.submit_count",
    "(a.telemetry->'agent_cost'->>'amount')::numeric AS agent_cost_amount",
    "(a.telemetry->'simulator_cost'->>'amount')::numeric AS simulator_cost_amount",
    "(a.telemetry->>'agent_turns')::int AS agent_turns",
)


def upgrade() -> None:
    op.execute(
        "CREATE VIEW ops_read.eval_experiments WITH (security_barrier=true) AS\n"
        f"SELECT {', '.join(_EXPERIMENT_COLUMNS)}\n"
        "FROM eval.experiment"
    )
    op.execute(
        "CREATE VIEW ops_read.eval_attempts WITH (security_barrier=true) AS\n"
        f"SELECT {', '.join(_ATTEMPT_COLUMNS)}\n"
        "FROM eval.task_attempt a LEFT JOIN eval.task_result r "
        "ON r.attempt_id = a.attempt_id"
    )
    op.execute("ALTER VIEW ops_read.eval_experiments OWNER TO evaluation_owner")
    op.execute("ALTER VIEW ops_read.eval_attempts OWNER TO evaluation_owner")
    op.execute("REVOKE ALL ON ops_read.eval_experiments FROM PUBLIC")
    op.execute("REVOKE ALL ON ops_read.eval_attempts FROM PUBLIC")
    op.execute("GRANT SELECT ON ops_read.eval_experiments TO agent_reader")
    op.execute("GRANT SELECT ON ops_read.eval_attempts TO agent_reader")


def downgrade() -> None:
    op.execute("DROP VIEW ops_read.eval_attempts")
    op.execute("DROP VIEW ops_read.eval_experiments")
