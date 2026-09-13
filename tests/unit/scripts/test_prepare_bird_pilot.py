import json
from pathlib import Path

import pytest

from scripts.prepare_bird_pilot import (
    DEFAULT_ADK_ROOT,
    _checker_env,
    _gitignore_covers,
    compare_metadata_baseline,
    select_pilot_tasks,
    verify_frozen_revisions,
    write_ledger_seed,
)


def test_checker_env_allowlists_runtime_vars_and_strips_secrets(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "C:/fake-bin")
    monkeypatch.setenv("TEMP", "C:/fake-temp")
    monkeypatch.setenv("TMP", "C:/fake-temp")
    monkeypatch.setenv("USERPROFILE", "C:/fake-user")
    monkeypatch.setenv("LOCALAPPDATA", "C:/fake-local")
    monkeypatch.setenv("SYSTEMROOT", "C:/fake-windows")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-secret")
    monkeypatch.setenv("UNRELATED_VAR", "noise")

    env = _checker_env()

    assert env["PATH"] == "C:/fake-bin"
    assert env["TEMP"] == "C:/fake-temp"
    assert env["TMP"] == "C:/fake-temp"
    assert env["USERPROFILE"] == "C:/fake-user"
    assert env["LOCALAPPDATA"] == "C:/fake-local"
    assert env["SYSTEMROOT"] == "C:/fake-windows"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert "DEEPSEEK_API_KEY" not in env
    assert "UNRELATED_VAR" not in env


def test_default_adk_root_matches_official_checker_location() -> None:
    assert (DEFAULT_ADK_ROOT / "env" / "check_db_metadata.py").as_posix() == (
        "_upstream/BIRD-Interact/env/check_db_metadata.py"
    )


def _dataset(tmp_path: Path, count: int) -> Path:
    tasks = []
    for index in range(count):
        tasks.append(
            {
                "instance_id": f"task-{index:03d}",
                "selected_database": f"db_{index % 3}",
                "user_query_ambiguity": {
                    "critical_ambiguity": [{"q": f"q{index}"}]
                },
                "knowledge_ambiguity": [],
                "sol_sql": f"SELECT {index}",  # GT-bearing field on purpose
                "test_cases": [{"case": index}],
            }
        )
    path = tmp_path / "bird_interact_data.jsonl"
    path.write_text(
        "\n".join(json.dumps(task) for task in tasks) + "\n", encoding="utf-8"
    )
    return path


def test_verify_frozen_revisions_reports_fixture_and_digest() -> None:
    report = verify_frozen_revisions()

    assert report["source_revision"] == "451fe2c3518ee1cf908d8139e2913483bd519381"
    assert report["image_digest_expected"].startswith("sha256:")
    assert report["image_digest_state"] in {
        "match",
        "mismatch:" ,
        "image-not-local",
        "docker-unavailable",
    } or str(report["image_digest_state"]).startswith("mismatch:")


_REAL_CHECKER_OUTPUT_SAMPLE = (
    "🔍 Checking database metadata for 127.0.0.1:5433\n"
    "✅ Found 22 databases: archeology_scan, sports_events\n"
    "\n"
    "📊 Database Metadata Summary for 127.0.0.1:5433\n"
    "============================================================\n"
    "📈 Total Databases: 22\n"
    "📋 Total Tables: 244\n"
    "📋 Tables with Data: 244\n"
    "🔢 Total Columns: 2011\n"
    "📊 Total Rows: 273,571\n"
    "📈 Avg Rows per Table: 1,121.19\n"
    "💾 Total Size: 273.95 MB\n"
    "\n"
    "🎯 Expected Database Check:\n"
    "   Expected: 22\n"
    "   Present: 22 ✅\n"
)


def test_metadata_baseline_comparison_matches_real_checker_output() -> None:
    report = compare_metadata_baseline(_REAL_CHECKER_OUTPUT_SAMPLE)

    assert report["parsed"] == {
        "databases": 22,
        "tables": 244,
        "columns": 2011,
        "rows": 273571,
    }
    assert report["all_match"] is True


def test_metadata_baseline_comparison_ignores_host_ip_in_header() -> None:
    report = compare_metadata_baseline(_REAL_CHECKER_OUTPUT_SAMPLE)

    # "database metadata for 127.0.0.1" must not be parsed as the db count
    assert report["parsed"]["databases"] != 127


def test_metadata_baseline_comparison_detects_drift() -> None:
    output = _REAL_CHECKER_OUTPUT_SAMPLE.replace("Total Tables: 244", "Total Tables: 243")
    report = compare_metadata_baseline(output)
    assert report["all_match"] is False
    assert report["parsed"]["tables"] == 243


def test_selection_rejects_evaluator_only_paths(tmp_path: Path) -> None:
    dataset = tmp_path / "evaluator_only" / "data.jsonl"
    dataset.parent.mkdir(parents=True)
    dataset.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="evaluator-only"):
        select_pilot_tasks(dataset, tmp_path / "out")


def test_selection_refuses_unignored_gt_split_target(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path, 4)
    monkey_dir = Path.cwd()

    import os

    os.chdir(tmp_path)
    try:
        (tmp_path / ".gitignore").write_text("other\n", encoding="utf-8")
        with pytest.raises(ValueError, match="not gitignored"):
            select_pilot_tasks(dataset, tmp_path / "outputs" / "bird-pilot")
    finally:
        os.chdir(monkey_dir)


def test_selection_writes_public_summary_and_gt_splits(tmp_path: Path, monkeypatch) -> None:
    dataset = _dataset(tmp_path, 8)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text(
        "outputs/bird-pilot/task-data/\n", encoding="utf-8"
    )
    out_dir = tmp_path / "outputs" / "bird-pilot"

    report = select_pilot_tasks(dataset, out_dir, count=4, seed=7)

    assert report["selected"] == 4
    summary = json.loads((out_dir / "task-selection.json").read_text(encoding="utf-8"))
    assert summary["count"] == 4
    modes = {item["mode"] for item in summary["selections"]}
    assert modes == {"c", "a"}
    summary_text = (out_dir / "task-selection.json").read_text(encoding="utf-8")
    for token in ("sol_sql", "test_cases"):
        assert token not in summary_text  # public fields only

    splits = sorted((out_dir / "task-data").glob("*.jsonl"))
    assert len(splits) == 4
    split = json.loads(splits[0].read_text(encoding="utf-8"))
    assert "sol_sql" in split  # the orchestrator-consumed split carries the task record
    assert "_m_amb" not in split and "_database" not in split


def test_selection_rejects_odd_counts(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path, 2)
    with pytest.raises(ValueError, match="evenly"):
        select_pilot_tasks(dataset, tmp_path / "out", count=3)


def test_ledger_seed_contains_frozen_formula_fields(tmp_path: Path) -> None:
    ledger_path = write_ledger_seed(tmp_path)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))

    assert ledger["ceiling_yuan"] == 180.0
    assert ledger["reserve_yuan"] == 20.0
    assert ledger["spent_so_far_yuan"] == 0.0
    assert ledger["full_remaining_upper_bound_yuan"] is None
    assert ledger["recheck_every_completed_full_tasks"] == 25
    assert "escalate to the user" in ledger["decision_rule"]


def test_gitignore_guard_checks_exact_directory(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(
        "outputs/bird-pilot/task-data/\n", encoding="utf-8"
    )
    assert _gitignore_covers(tmp_path / "outputs" / "bird-pilot" / "task-data")
    assert not _gitignore_covers(tmp_path / "outputs" / "bird-pilot")
