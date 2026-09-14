"""The Day 6 eval read view migration pins the public whitelist."""

import re
from pathlib import Path

MIGRATION = (
    Path(__file__)
    .parents[2]
    / "db"
    / "migrations"
    / "versions"
    / "0007_day6_eval_read_view.py"
)

EXPERIMENT_COLUMNS = [
    "experiment_id",
    "purpose",
    "config_hash",
    "created_at",
    "closed_at",
]

ATTEMPT_COLUMNS = [
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
]


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_experiment_view_exposes_exactly_the_public_whitelist() -> None:
    source = _source()
    assert (
        "CREATE VIEW ops_read.eval_experiments WITH (security_barrier=true)"
        in source
    )
    block = re.search(
        r"_EXPERIMENT_COLUMNS = \(\n(?P<block>.*?)\n\)", source, re.DOTALL
    ).group("block")
    assert re.findall(r"[a-z_]+", block) == EXPERIMENT_COLUMNS


def test_attempt_view_exposes_exactly_the_public_whitelist() -> None:
    source = _source()
    assert (
        "CREATE VIEW ops_read.eval_attempts WITH (security_barrier=true)"
        in source
    )
    block = re.search(
        r"_ATTEMPT_COLUMNS = \(\n(?P<block>.*?)\n\)", source, re.DOTALL
    ).group("block")
    columns = [line.strip().rstrip(",").strip('"') for line in block.splitlines()]
    columns = [re.sub(r"\s+", " ", column) for column in columns if column]
    assert columns == ATTEMPT_COLUMNS


def test_view_owner_is_the_evaluation_owner_and_reader_is_scoped() -> None:
    source = _source()
    assert "ALTER VIEW ops_read.eval_experiments OWNER TO evaluation_owner" in source
    assert "ALTER VIEW ops_read.eval_attempts OWNER TO evaluation_owner" in source
    assert "REVOKE ALL ON ops_read.eval_experiments FROM PUBLIC" in source
    assert "REVOKE ALL ON ops_read.eval_attempts FROM PUBLIC" in source
    assert "GRANT SELECT ON ops_read.eval_experiments TO agent_reader" in source
    assert "GRANT SELECT ON ops_read.eval_attempts TO agent_reader" in source


def test_base_table_grants_stay_untouched() -> None:
    source = _source()
    # the eval ACL is Day 5 frozen: this migration must not widen it
    assert "GRANT SELECT ON eval" not in source
    assert "GRANT " not in source.replace(
        "GRANT SELECT ON ops_read.eval_experiments TO agent_reader", ""
    ).replace("GRANT SELECT ON ops_read.eval_attempts TO agent_reader", "")


def test_revision_id_fits_alembic_varchar32() -> None:
    assert len(revision_candidate(_source())) <= 32


def revision_candidate(source: str) -> str:
    return re.search(r'revision: str = "(?P<id>[^"]+)"', source).group("id")
