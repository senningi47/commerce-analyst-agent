"""Task 12 preflight for the 20-episode BIRD Pilot (zero API requests).

Offline-safe by default. The script verifies the frozen revisions, optionally
re-runs the official database metadata checker, stratifies 20 tasks from the
full dataset into per-task data files, and seeds the §16.4 budget ledger.

GT discipline (v0.3 §5.1, §14.4):
- the dataset path must NOT contain `evaluator_only`;
- per-task data files (GT-bearing) are written ONLY under a gitignored
  directory, enforced before the first byte is written;
- `task-selection.json` and all stdout carry public fields only.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from commerce_agent.evaluation.contracts import load_official_contract

EXPECTED_IMAGE_DIGEST = (
    "sha256:de4b88f6f3211238318a3dc265914f4e38122ec67ece642bbf3df0c3b781040c"
)
EXPECTED_METADATA_BASELINE = {
    "databases": 22,
    "tables": 244,
    "columns": 2011,
    "rows": 273571,
}
FORBIDDEN_PATH_TOKENS = ("evaluator_only", "sol_sql", "test_cases")
DEFAULT_PILOT_COUNT = 20
DEFAULT_LEDGER_CEILING_YUAN = 180.0
DEFAULT_LEDGER_RESERVE_YUAN = 20.0

_METADATA_BASELINE_PATTERNS = {
    "databases": re.compile(r"databases?\D{0,20}?(\d[\d,]*)", re.IGNORECASE),
    "tables": re.compile(r"tables?\D{0,20}?(\d[\d,]*)", re.IGNORECASE),
    "columns": re.compile(r"columns?\D{0,20}?(\d[\d,]*)", re.IGNORECASE),
    "rows": re.compile(r"rows?\D{0,20}?(\d[\d,]*)", re.IGNORECASE),
}


def verify_frozen_revisions() -> dict[str, object]:
    """Compare the frozen fixture revision and the Day 1E image digest."""

    contract = load_official_contract()
    image_state = "not-checked"
    try:
        completed = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{index .RepoDigests 0}}",
                "shawnxxh/bird-interact-postgresql-full:latest",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            digest = completed.stdout.strip().split("@")[-1]
            image_state = "match" if digest == EXPECTED_IMAGE_DIGEST else f"mismatch:{digest}"
        else:
            image_state = "image-not-local"
    except OSError:
        image_state = "docker-unavailable"
    return {
        "source_revision": contract.source_revision,
        "image_digest_expected": EXPECTED_IMAGE_DIGEST,
        "image_digest_state": image_state,
    }


def compare_metadata_baseline(checker_output: str) -> dict[str, object]:
    """Parse the official check_db_metadata.py output against the Day 1E baseline."""

    parsed: dict[str, int | None] = {}
    for name, pattern in _METADATA_BASELINE_PATTERNS.items():
        match = pattern.search(checker_output)
        if match is None:
            parsed[name] = None
        else:
            parsed[name] = int(match.group(1).replace(",", ""))
    matches = {
        name: (parsed[name] == EXPECTED_METADATA_BASELINE[name])
        for name in EXPECTED_METADATA_BASELINE
    }
    return {
        "parsed": parsed,
        "expected": EXPECTED_METADATA_BASELINE,
        "all_match": all(matches.values()) and all(value is not None for value in parsed.values()),
    }


def run_metadata_checker(adk_root: Path, port: int = 5433) -> dict[str, object]:
    """Re-run the official checker (Day 1E command) and compare to the baseline."""

    completed = subprocess.run(
        [
            "uv",
            "run",
            "--with",
            "psycopg2-binary",
            "python",
            str(adk_root / "env" / "check_db_metadata.py"),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--version",
            "full",
        ],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        env={"PYTHONIOENCODING": "utf-8", "PATH": __import__("os").environ["PATH"]},
    )
    return {
        "returncode": completed.returncode,
        "comparison": compare_metadata_baseline(completed.stdout + completed.stderr),
    }


def _forbidden_path(path: Path) -> bool:
    lowered = path.as_posix().lower()
    return any(token in lowered for token in FORBIDDEN_PATH_TOKENS)


def _gitignore_covers(env_dir: Path) -> bool:
    """True when some .gitignore above `env_dir` ignores the directory itself."""

    current = env_dir.resolve()
    while True:
        gitignore = current / ".gitignore"
        if gitignore.is_file():
            ignored = {
                line.strip().rstrip("/")
                for line in gitignore.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            }
            relative = env_dir.resolve().relative_to(current).as_posix()
            if relative in ignored or relative.rstrip("/") in ignored:
                return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _ambiguity_count(task: dict[str, Any]) -> int:
    critical = task.get("user_query_ambiguity", {}) or {}
    return len(critical.get("critical_ambiguity", []) or []) + len(
        task.get("knowledge_ambiguity", []) or []
    )


def select_pilot_tasks(
    dataset_path: Path,
    out_dir: Path,
    *,
    count: int = DEFAULT_PILOT_COUNT,
    seed: int = 7,
) -> dict[str, object]:
    """Stratify tasks into per-mode selections; write public summary + GT splits."""

    if count % 2 != 0:
        raise ValueError("pilot count must split evenly between c and a modes")
    if _forbidden_path(dataset_path):
        raise ValueError("dataset path must not reference evaluator-only content")
    task_data_dir = out_dir / "task-data"
    if not _gitignore_covers(task_data_dir):
        raise ValueError(
            f"refusing to write GT-bearing splits: {task_data_dir} is not gitignored"
        )

    tasks: list[dict[str, Any]] = []
    with dataset_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                tasks.append(json.loads(line))
    if not tasks:
        raise ValueError("dataset contains no tasks")

    for task in tasks:
        task["_m_amb"] = _ambiguity_count(task)
        task["_database"] = str(task.get("selected_database", "unknown"))
    ordered = sorted(tasks, key=lambda task: (task["_database"], task["_m_amb"], task["instance_id"]))
    per_mode = count // 2
    rng = random.Random(seed)

    selection: list[dict[str, object]] = []
    for mode in ("c", "a"):
        pool = [task for task in ordered]
        rng.shuffle(pool)
        strata: dict[str, list[dict[str, Any]]] = {}
        for task in pool:
            strata.setdefault(task["_database"], []).append(task)
        round_robin: list[dict[str, Any]] = []
        while len(round_robin) < per_mode and any(strata.values()):
            for database in sorted(strata):
                if strata[database] and len(round_robin) < per_mode:
                    round_robin.append(strata[database].pop(0))
        for task in round_robin:
            selection.append(
                {
                    "task_id": task["instance_id"],
                    "mode": mode,
                    "basis": {
                        "strategy": "database+ambiguity-fallback",
                        "selected_database": task["_database"],
                        "ambiguity_count": task["_m_amb"],
                    },
                }
            )
            split_dir = out_dir / "task-data"
            split_dir.mkdir(parents=True, exist_ok=True)
            split_path = split_dir / f"{task['instance_id']}-{mode}.jsonl"
            clean = {key: value for key, value in task.items() if not key.startswith("_")}
            split_path.write_text(
                json.dumps(clean, ensure_ascii=False) + "\n", encoding="utf-8"
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "task-selection.json"
    summary_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "strategy": "database+ambiguity-fallback (v0.3 §16.3 fallback; no BI/DM labels assumed)",
                "count": len(selection),
                "seed": seed,
                "selections": selection,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "selected": len(selection),
        "databases_covered": len({item["basis"]["selected_database"] for item in selection}),
        "summary_path": str(summary_path),
        "task_data_dir": str(task_data_dir),
    }


def write_ledger_seed(out_dir: Path) -> Path:
    """Seed the §16.4 dynamic budget ledger; measured values stay null."""

    ledger_dir = out_dir / "bird-budget"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = ledger_dir / "pilot-ledger.json"
    ledger_path.write_text(
        json.dumps(
            {
                "formula": "spent_so_far + full_remaining_upper_bound + product_remaining_upper_bound <= ceiling",
                "ceiling_yuan": DEFAULT_LEDGER_CEILING_YUAN,
                "reserve_yuan": DEFAULT_LEDGER_RESERVE_YUAN,
                "spent_so_far_yuan": 0.0,
                "full_remaining_upper_bound_yuan": None,
                "product_remaining_upper_bound_yuan": None,
                "recheck_every_completed_full_tasks": 25,
                "decision_rule": "if the projected total would cross the ceiling, pause safely and escalate to the user",
                "note": "measured fields are filled only from actual usage/balance evidence",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return ledger_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=None, help="full dataset JSONL (GT-bearing)")
    parser.add_argument("--count", type=int, default=DEFAULT_PILOT_COUNT)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--run-db-check", action="store_true")
    parser.add_argument("--adk-root", type=Path, default=Path("_upstream/BIRD-Interact/BIRD-Interact-ADK"))
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/bird-pilot"))
    args = parser.parse_args(argv)

    report: dict[str, object] = {"revisions": verify_frozen_revisions()}
    if args.run_db_check:
        report["metadata_baseline"] = run_metadata_checker(args.adk_root)
    if args.dataset is not None:
        report["selection"] = select_pilot_tasks(
            args.dataset, args.out_dir, count=args.count, seed=args.seed
        )
    report["ledger_seed"] = str(write_ledger_seed(args.out_dir.parent))

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("preflight complete: no API requests were made")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
