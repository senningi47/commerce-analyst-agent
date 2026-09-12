"""Create Day 5 evaluation experiment, attempt, result, and trace objects.

Revision ID: 0005_day5_evaluation_schema
Revises: 0004_day4_product_operations
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_day5_evaluation_schema"
down_revision: str | None = "0004_day4_product_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPGRADE_STATEMENTS = (
    "CREATE SCHEMA eval",
    (
        "CREATE TABLE eval.experiment (\n"
        "    experiment_id text PRIMARY KEY,\n"
        "    purpose text NOT NULL CHECK (purpose IN "
        "('pilot','full','ablation_repair','rag_ab','product')),\n"
        "    config_hash char(64) NOT NULL,\n"
        "    created_at timestamptz NOT NULL DEFAULT now(),\n"
        "    closed_at timestamptz\n"
        ")"
    ),
    (
        "CREATE TABLE eval.task_attempt (\n"
        "    attempt_id uuid PRIMARY KEY,\n"
        "    run_id uuid NOT NULL,\n"
        "    experiment_id text NOT NULL REFERENCES eval.experiment(experiment_id),\n"
        "    task_id text NOT NULL,\n"
        "    mode text NOT NULL CHECK (mode IN ('c','a')),\n"
        "    attempt_seq integer NOT NULL CHECK (attempt_seq >= 1),\n"
        "    status text NOT NULL CHECK (status IN ('pending','running','succeeded',"
        "'failed','infrastructure_error','interrupted')),\n"
        "    error_class text,\n"
        "    started_at timestamptz NOT NULL,\n"
        "    finished_at timestamptz,\n"
        "    telemetry jsonb NOT NULL DEFAULT '{}'::jsonb,\n"
        "    CONSTRAINT task_attempt_unique_identity UNIQUE (run_id, task_id, mode, attempt_seq)\n"
        ")"
    ),
    (
        "CREATE TABLE eval.task_result (\n"
        "    attempt_id uuid PRIMARY KEY REFERENCES eval.task_attempt(attempt_id),\n"
        "    reward numeric,\n"
        "    phase1_passed boolean,\n"
        "    phase2_passed boolean,\n"
        "    rounds integer,\n"
        "    tool_calls integer,\n"
        "    submit_count integer\n"
        ")"
    ),
    (
        "CREATE TABLE eval.bird_trace_event (\n"
        "    attempt_id uuid NOT NULL REFERENCES eval.task_attempt(attempt_id),\n"
        "    phase text NOT NULL,\n"
        "    sequence integer NOT NULL,\n"
        "    event_type text NOT NULL,\n"
        "    payload jsonb NOT NULL,\n"
        "    recorded_at timestamptz NOT NULL DEFAULT now(),\n"
        "    CONSTRAINT bird_trace_event_identity PRIMARY KEY (attempt_id, phase, sequence)\n"
        ")"
    ),
    "ALTER SCHEMA eval OWNER TO evaluation_owner",
    "ALTER TABLE eval.experiment OWNER TO evaluation_owner",
    "ALTER TABLE eval.task_attempt OWNER TO evaluation_owner",
    "ALTER TABLE eval.task_result OWNER TO evaluation_owner",
    "ALTER TABLE eval.bird_trace_event OWNER TO evaluation_owner",
    "REVOKE ALL ON SCHEMA eval FROM PUBLIC",
    "REVOKE ALL ON ALL TABLES IN SCHEMA eval FROM PUBLIC",
    "GRANT USAGE ON SCHEMA eval TO evaluation_writer",
    "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA eval TO evaluation_writer",
)


def upgrade() -> None:
    for statement in _UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP SCHEMA eval CASCADE")
