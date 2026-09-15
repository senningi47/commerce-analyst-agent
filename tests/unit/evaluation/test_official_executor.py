"""Unit tests for the official-orchestrator episode executor."""

import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from commerce_agent.evaluation._official import OfficialOrchestratorEpisodeExecutor
from commerce_agent.evaluation.contracts import AttemptRecord, EvalTaskStatus
from commerce_agent.evaluation.episode import EpisodeTask

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

FAKE_RUNNER = '''import argparse, json, sys, time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--mode")
parser.add_argument("--data")
parser.add_argument("--output")
parser.add_argument("--limit")
parser.add_argument("--concurrency")
args = parser.parse_args()

print("stdout-marker-from-official-orchestrator")
print("stderr-marker-from-official-orchestrator", file=sys.stderr)

if "--sleep" in sys.argv:
    time.sleep(30)

payload = {"results": [{"total_reward": 0.5, "phase1_passed": True, "phase2_passed": False}]}
if "--error" in sys.argv:
    payload["results"][0]["error"] = "something broke"
Path(args.output).write_text(json.dumps(payload), encoding="utf-8")
'''


def make_attempt() -> AttemptRecord:
    return AttemptRecord(
        attempt_id=uuid4(),
        run_id=uuid4(),
        experiment_id="day7",
        task_id="task-1",
        mode="c",
        attempt_seq=1,
        started_at=NOW,
    )


def make_executor(tmp_path: Path, *, timeout: int = 30) -> OfficialOrchestratorEpisodeExecutor:
    return OfficialOrchestratorEpisodeExecutor(
        adk_root=tmp_path / "adk",
        python_executable=sys.executable,
        data_file_for=lambda task: tmp_path / "task-data" / f"{task.task_id}-{task.mode}.jsonl",
        output_dir=tmp_path / "episodes",
        episode_timeout_seconds=timeout,
    )


def seed_fake_adk(tmp_path: Path) -> None:
    orchestrator = tmp_path / "adk" / "orchestrator"
    orchestrator.mkdir(parents=True)
    (orchestrator / "runner.py").write_text(FAKE_RUNNER, encoding="utf-8")


def test_successful_episode_persists_subprocess_streams(tmp_path: Path) -> None:
    """Pit 68: the orchestrator subprocess's stdout/stderr must land on disk
    (agent-visible content, not GT) so official_task_error is traceable
    offline instead of burning paid diagnostic runs."""
    seed_fake_adk(tmp_path)
    executor = make_executor(tmp_path)
    attempt = make_attempt()

    outcome = asyncio.run(
        executor.execute(EpisodeTask(task_id="task-1", mode="c"), attempt)
    )

    assert outcome.status is EvalTaskStatus.SUCCEEDED
    streams = tmp_path / "episodes"
    assert "stdout-marker-from-official-orchestrator" in (
        streams / f"{attempt.attempt_id}.stdout.txt"
    ).read_text(encoding="utf-8")
    assert "stderr-marker-from-official-orchestrator" in (
        streams / f"{attempt.attempt_id}.stderr.txt"
    ).read_text(encoding="utf-8")


def test_failed_process_still_persists_streams(tmp_path: Path) -> None:
    seed_fake_adk(tmp_path)
    runner = tmp_path / "adk" / "orchestrator" / "runner.py"
    runner.write_text("import sys\nprint('died-hard', file=sys.stderr)\nraise SystemExit(3)\n", encoding="utf-8")
    executor = make_executor(tmp_path)
    attempt = make_attempt()

    outcome = asyncio.run(
        executor.execute(EpisodeTask(task_id="task-1", mode="c"), attempt)
    )

    assert outcome.status is EvalTaskStatus.INFRASTRUCTURE_ERROR
    assert outcome.error_class == "official_process_failed"
    assert "died-hard" in (
        tmp_path / "episodes" / f"{attempt.attempt_id}.stderr.txt"
    ).read_text(encoding="utf-8")


def test_timeout_kills_child_and_reports_infrastructure(tmp_path: Path) -> None:
    """A timed-out orchestrator must not linger as an orphan still calling the
    paid model API (pit 58 hardening)."""
    seed_fake_adk(tmp_path)
    runner = tmp_path / "adk" / "orchestrator" / "runner.py"
    runner.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    executor = make_executor(tmp_path, timeout=1)
    attempt = make_attempt()
    started = time.monotonic()

    outcome = asyncio.run(
        executor.execute(EpisodeTask(task_id="task-1", mode="c"), attempt)
    )

    assert outcome.status is EvalTaskStatus.INFRASTRUCTURE_ERROR
    assert outcome.error_class == "official_episode_timeout"
    assert time.monotonic() - started < 15  # killed promptly, not 30s


def test_orchestrator_env_pins_utf8_mode(tmp_path: Path) -> None:
    """Pit 68 follow-up (A4 b01): the official orchestrator opens its data
    file with the platform default codec — on Windows that is GBK and any
    out-of-range byte kills the episode with official_process_failed. The
    executor must pin PYTHONUTF8=1 for the child interpreter."""
    seed_fake_adk(tmp_path)
    executor = make_executor(tmp_path)
    env = executor._env()
    assert env["PYTHONUTF8"] == "1"
