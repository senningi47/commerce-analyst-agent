"""Command-line entry for the EvaluationRunner (stub or official executor)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from uuid import uuid4

from commerce_agent.evaluation._memory import InMemoryEvaluationStore
from commerce_agent.evaluation._official import OfficialOrchestratorEpisodeExecutor
from commerce_agent.evaluation._postgres import PostgresEvaluationStore
from commerce_agent.evaluation.contracts import EvalStateConflict
from commerce_agent.evaluation.episode import EpisodeTask
from commerce_agent.evaluation.runner import EvaluationEventLog, EvaluationRunner, RunnerConfig

_DEFAULT_DSN_ENV = "PRODUCT_EVALUATION_DATABASE_DSN"


def _load_task_list(path: Path) -> tuple[EpisodeTask, ...]:
    tasks: list[EpisodeTask] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        tasks.append(EpisodeTask.model_validate(json.loads(line)))
    if not tasks:
        raise SystemExit(f"task list is empty: {path}")
    return tuple(tasks)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BIRD EvaluationRunner")
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--purpose", required=True, choices=("pilot", "full", "ablation_repair", "rag_ab", "product"))
    parser.add_argument("--config-hash", required=True)
    parser.add_argument("--task-list", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--executor", required=True, choices=("stub", "official"))
    parser.add_argument("--store", choices=("postgres", "memory"), default="postgres")
    parser.add_argument("--dsn-env", default=_DEFAULT_DSN_ENV)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--stop-grace-seconds", type=int, default=60)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--adk-root", type=Path, default=None)
    parser.add_argument("--task-data-dir", type=Path, default=None)
    parser.add_argument("--episode-output-dir", type=Path, default=None)
    return parser


def _build_executor(args: argparse.Namespace) -> object:
    if args.executor == "stub":
        from commerce_agent.evaluation.contracts import EvalTaskStatus
        from commerce_agent.evaluation.episode import EpisodeOutcome, StubEpisodeExecutor

        def fallback(task: EpisodeTask, attempt: object) -> EpisodeOutcome:
            del task, attempt
            return EpisodeOutcome(
                attempt_id=uuid4(),
                status=EvalTaskStatus.FAILED,
                error_class="stub_executor_default",
            )

        return StubEpisodeExecutor([], fallback=fallback)
    if args.adk_root is None or args.task_data_dir is None or args.episode_output_dir is None:
        raise SystemExit(
            "official executor requires --adk-root, --task-data-dir, --episode-output-dir"
        )

    def data_file_for(task: EpisodeTask) -> Path:
        return args.task_data_dir / f"{task.task_id}-{task.mode}.jsonl"

    return OfficialOrchestratorEpisodeExecutor(
        adk_root=args.adk_root,
        python_executable=sys.executable,
        data_file_for=data_file_for,
        output_dir=args.episode_output_dir,
    )


def _bridge_signals(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)
    except NotImplementedError:
        # Windows ProActor loop: bridge through a polling task
        threading_flag = {"requested": False}

        def on_signal(signum: int, frame: object) -> None:
            del signum, frame
            threading_flag["requested"] = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, on_signal)

        async def poll() -> None:
            while not threading_flag["requested"] and not stop_event.is_set():
                await asyncio.sleep(0.2)
            stop_event.set()

        asyncio.create_task(poll())


async def _async_main(args: argparse.Namespace) -> int:
    store = (
        InMemoryEvaluationStore()
        if args.store == "memory"
        else PostgresEvaluationStore(dsn=os.environ[args.dsn_env])
    )
    config = RunnerConfig(
        experiment_id=args.experiment,
        purpose=args.purpose,
        config_hash=args.config_hash,
        task_list=_load_task_list(args.task_list),
        concurrency=args.concurrency,
        stop_grace_seconds=args.stop_grace_seconds,
        task_order_seed=args.seed,
    )
    stop_event = asyncio.Event()
    _bridge_signals(stop_event)
    runner = EvaluationRunner(
        store=store,
        executor=_build_executor(args),  # type: ignore[arg-type]
        events=EvaluationEventLog(args.events),
        config=config,
        stop_event=stop_event,
    )
    try:
        summary = await runner.run()
    except EvalStateConflict as error:
        print(f"runner stopped: {error.reason_code}")
        return 1
    print(
        f"experiment={summary.experiment_id} stopped={summary.stopped} "
        f"attempted={summary.attempted_episodes} completed={summary.completed_tasks} "
        f"unfinished={summary.unfinished_tasks}"
    )
    return 130 if summary.stopped else 0


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    exit_code = asyncio.run(_async_main(args))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
