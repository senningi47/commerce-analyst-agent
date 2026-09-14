"""The Day 6 trace read view migration pins the §18 payload whitelist."""

import re
from pathlib import Path

MIGRATION = (
    Path(__file__)
    .parents[2]
    / "db"
    / "migrations"
    / "versions"
    / "0006_day6_trace_read_view.py"
)

PUBLIC_COLUMNS = [
    "run_id",
    "attempt_id",
    "sequence",
    "event_type",
    "status",
    "safe_summary",
    "reason_code",
    "occurred_at",
]


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_view_exposes_exactly_the_public_whitelist() -> None:
    source = _source()
    assert (
        "CREATE VIEW ops_read.product_trace_events WITH (security_barrier=true)"
        in source
    )
    column_block = re.search(
        r"_VIEW_COLUMNS = \(\n(?P<block>.*?)\n\)", source, re.DOTALL
    ).group("block")
    columns = re.findall(r"[a-z_]+", column_block)
    assert columns == PUBLIC_COLUMNS


def test_view_grants_are_owner_only_and_reader_scoped() -> None:
    source = _source()
    assert "ALTER VIEW ops_read.product_trace_events OWNER TO ops_owner" in source
    assert "REVOKE ALL ON ops_read.product_trace_events FROM PUBLIC" in source
    assert "GRANT SELECT ON ops_read.product_trace_events TO agent_reader" in source
    # the raw audit table must never gain a SELECT grant from this migration
    assert "GRANT SELECT ON app.product_trace_event" not in source
    assert "GRANT SELECT ON app" not in source
