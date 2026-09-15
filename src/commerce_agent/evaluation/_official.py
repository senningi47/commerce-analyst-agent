"""Official-orchestrator episode executor: one subprocess per episode.

argv/env strictly follow the frozen contract (`load_official_contract()`):
`--limit 1 --concurrency 1` per invocation, a pre-split per-task data file
(produced by the Task 12 selection script — the runner process itself never
reads the dataset), and only the frozen env-name subset plus OS essentials.
Delivery is fail-closed: a nonzero exit or unparseable output becomes
`infrastructure_error` with a new attempt on resume — never a blind retry.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from commerce_agent.evaluation.contracts import (
    AttemptRecord,
    AttemptTelemetry,
    EpisodeResult,
    EvalStateConflict,
    EvalTaskStatus,
    load_official_contract,
)
from commerce_agent.evaluation.episode import EpisodeOutcome, EpisodeTask

_MODE_WIRE = {"c": "c-interact", "a": "a-interact"}
_OS_ENV_ESSENTIALS = ("PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP")


class OfficialOrchestratorEpisodeExecutor:
    def __init__(
        self,
        *,
        adk_root: Path,
        python_executable: str,
        data_file_for: Callable[[EpisodeTask], Path],
        output_dir: Path,
        launcher: Callable[[list[str], dict[str, str]], Any] | None = None,
        episode_timeout_seconds: int = 1_900,
    ) -> None:
        self._adk_root = adk_root
        self._python = python_executable
        self._data_file_for = data_file_for
        self._output_dir = output_dir
        self._launcher = launcher
        self._timeout = episode_timeout_seconds
        contract = load_official_contract()
        self._argv_pattern = contract.orchestrator_cli.argv_pattern
        self._env_names = frozenset(contract.orchestrator_env_names)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def _argv(self, task: EpisodeTask, data_file: Path, output_file: Path) -> list[str]:
        values = {
            "mode": _MODE_WIRE[task.mode],
            "data": str(data_file),
            "output": str(output_file),
            "limit": "1",
            "concurrency": "1",
        }
        argv: list[str] = [self._python, str(self._adk_root / "orchestrator" / "runner.py")]
        for token in self._argv_pattern:
            if token.startswith("--"):
                argv.append(token)
            else:
                key = token.strip("{}")
                if key not in values:
                    raise EvalStateConflict("orchestrator_argv_unknown_placeholder")
                argv.append(values[key])
        return argv

    def _env(self) -> dict[str, str]:
        env = {name: os.environ[name] for name in _OS_ENV_ESSENTIALS if name in os.environ}
        for name in sorted(self._env_names):
            if name in os.environ:
                env[name] = os.environ[name]
        # the official orchestrator opens its data file with the platform
        # default codec — on Windows that is GBK and any out-of-range byte
        # kills the episode (A4 b01 finding); pin the child interpreter to
        # UTF-8 regardless of host locale
        env["PYTHONUTF8"] = "1"
        return env

    async def execute(self, task: EpisodeTask, attempt: AttemptRecord) -> EpisodeOutcome:
        data_file = self._data_file_for(task)
        output_file = self._output_dir / f"{attempt.attempt_id}.json"
        argv = self._argv(task, data_file, output_file)
        env = self._env()
        try:
            if self._launcher is not None:
                return_code = await asyncio.to_thread(self._launcher, argv, env)
            else:
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(), timeout=self._timeout
                    )
                except TimeoutError:
                    # a lingering orchestrator keeps calling the paid model API
                    process.kill()
                    await process.wait()
                    return self._infrastructure(attempt, "official_episode_timeout")
                self._persist_streams(attempt.attempt_id, stdout, stderr)
                return_code = process.returncode
        except TimeoutError:
            return self._infrastructure(attempt, "official_episode_timeout")
        except OSError:
            return self._infrastructure(attempt, "official_spawn_failed")
        if return_code != 0:
            return self._infrastructure(attempt, "official_process_failed")

        try:
            payload = json.loads(output_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._infrastructure(attempt, "official_output_unreadable")
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            return self._infrastructure(attempt, "official_output_shape_invalid")
        record = results[0]
        if not isinstance(record, dict):
            return self._infrastructure(attempt, "official_output_shape_invalid")

        error_class = record.get("error")
        if error_class is not None:
            return EpisodeOutcome(
                attempt_id=attempt.attempt_id,
                status=EvalTaskStatus.FAILED,
                error_class="official_task_error",
            )
        return EpisodeOutcome(
            attempt_id=attempt.attempt_id,
            status=EvalTaskStatus.SUCCEEDED,
            result=EpisodeResult(
                reward=_as_reward(record.get("total_reward")),
                phase1_passed=_as_bool(record.get("phase1_passed")),
                phase2_passed=_as_bool(record.get("phase2_passed")),
            ),
            telemetry=AttemptTelemetry(
                wall_clock_ms=_as_ms(record.get("elapsed_seconds"))
            ),
        )

    def _persist_streams(
        self, attempt_id: Any, stdout: bytes | None, stderr: bytes | None
    ) -> None:
        """Pit 68: persist orchestrator subprocess diagnostics (agent-visible
        content, not GT) so official_process_failed / official_task_error are
        traceable offline instead of burning paid diagnostic runs."""
        for suffix, payload in (("stdout", stdout), ("stderr", stderr)):
            if payload is None:
                continue
            (self._output_dir / f"{attempt_id}.{suffix}.txt").write_bytes(payload)

    def _infrastructure(self, attempt: AttemptRecord, reason: str) -> EpisodeOutcome:
        return EpisodeOutcome(
            attempt_id=attempt.attempt_id,
            status=EvalTaskStatus.INFRASTRUCTURE_ERROR,
            error_class=reason,
        )


def _as_reward(value: Any) -> float | None:
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return None


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_ms(value: Any) -> int | None:
    if isinstance(value, (int, float)) and value >= 0:
        return int(value * 1000)
    return None
