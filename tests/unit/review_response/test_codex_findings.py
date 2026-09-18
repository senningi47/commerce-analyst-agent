"""Regression tests for the codex-review findings (2026-09-18)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from commerce_agent.evaluation._memory import InMemoryEvaluationStore
from commerce_agent.evaluation.contracts import EpisodeResult, EvalStateConflict, EvalTaskStatus
from commerce_agent.evaluation.episode import EpisodeOutcome, EpisodeTask, StubEpisodeExecutor
from commerce_agent.evaluation.runner import EvaluationRunner, RunnerConfig
from commerce_agent.model.contracts import ToolCall, ToolCallOutput
from commerce_agent.product_eval.question_eval import parse_sql_candidate
from commerce_agent.product_eval.scoring import results_match
from commerce_agent.query_engine._ast_policy import AstPolicy
from commerce_agent.query_engine._postgres import _normalize_scalar
from commerce_agent.query_engine.errors import SqlPolicyViolation

CONFIG_HASH = "a" * 64


class _NullEventLog:
    def append(self, event: dict[str, object]) -> None:
        pass


def _outcome() -> EpisodeOutcome:
    return EpisodeOutcome(
        status=EvalTaskStatus.SUCCEEDED, result=EpisodeResult(reward=1.0), attempt_id=uuid4()
    )


class _StopAfterFirst(StubEpisodeExecutor):
    def __init__(self) -> None:
        super().__init__([])
        self.executed: list[str] = []
        self.stop_event = asyncio.Event()

    async def execute(self, task: EpisodeTask, attempt: object) -> EpisodeOutcome:
        self.executed.append(task.task_id)
        if len(self.executed) == 1:
            self.stop_event.set()
        await asyncio.sleep(0.01)
        return _outcome()


def _runner(store, executor, tasks) -> EvaluationRunner:
    return EvaluationRunner(
        store=store,
        executor=executor,
        events=_NullEventLog(),
        config=RunnerConfig(
            experiment_id="fx",
            purpose="pilot",
            config_hash=CONFIG_HASH,
            task_list=tuple(tasks),
            concurrency=1,
            stop_grace_seconds=1,
        ),
        stop_event=getattr(executor, "stop_event", None),
    )


def test_codex_f4_queued_tasks_do_not_start_after_stop() -> None:
    tasks = [EpisodeTask(task_id=f"t{i}", mode="c") for i in range(5)]
    store = InMemoryEvaluationStore()
    executor = _StopAfterFirst()
    summary = asyncio.run(_runner(store, executor, tasks).run())
    assert executor.executed == ["t0"]
    assert summary.stopped is True
    assert store.unfinished_attempts("fx") == ()


class _StateConflictExecutor(StubEpisodeExecutor):
    def __init__(self) -> None:
        super().__init__([])

    async def execute(self, task: EpisodeTask, attempt: object) -> EpisodeOutcome:
        raise EvalStateConflict("synthetic_state_conflict")


def test_codex_f6_state_conflict_propagates_not_exit_zero() -> None:
    tasks = [EpisodeTask(task_id="t0", mode="c")]
    with pytest.raises(EvalStateConflict):
        asyncio.run(
            _runner(InMemoryEvaluationStore(), _StateConflictExecutor(), tasks).run()
        )


def test_codex_f1_type_anchored_scoring_matches_queryengine_boundary() -> None:
    """The QueryEngine stringifies Decimal; the reference path keeps it.
    A numeric reference must anchor the parse (codex F1)."""
    gold_rows = [{"total": Decimal("1.20")}]
    agent_rows = [{"total": _normalize_scalar(Decimal("1.20"))}]
    assert results_match(agent_rows, gold_rows, ["total"]) is True


def test_codex_f2_cell_bagging_rejected() -> None:
    assert (
        results_match(
            [{"a": 1, "b": 10}, {"a": 2, "b": 20}],
            [{"a": 1, "b": 10}, {"a": 20, "b": 2}],
            ["a", "b"],
        )
        is False
    )


def test_codex_f7_identity_wrappers_rejected() -> None:
    policy = AstPolicy()
    for sql in (
        "SELECT (seller_id) AS x FROM retail.sellers LIMIT 1",
        "SELECT COALESCE(seller_id, '') AS x FROM retail.sellers LIMIT 1",
        "SELECT seller_id || '' AS x FROM retail.sellers LIMIT 1",
        "SELECT MIN(seller_id) AS x FROM retail.sellers",
    ):
        with pytest.raises(SqlPolicyViolation):
            policy.validate(sql)
    # counting is an irreversible statistic and stays allowed
    policy.validate("SELECT COUNT(DISTINCT seller_id) AS n FROM retail.sellers")


def test_codex_f11_window_and_scalar_subquery_do_not_mark_aggregate() -> None:
    policy = AstPolicy()
    with pytest.raises(SqlPolicyViolation):
        policy.validate(
            "SELECT order_status, (SELECT COUNT(*) FROM retail.customers) AS n "
            "FROM retail.orders"
        )
    with pytest.raises(SqlPolicyViolation):
        policy.validate("SELECT COUNT(*) OVER () FROM retail.orders")


def test_codex_f9_shortcut_honors_time_window() -> None:
    from commerce_agent.evaluation.spool_importer import (
        AttemptRow,
        SpoolUsageRecord,
        assign_attempts,
    )

    now = datetime.now(UTC)
    record = SpoolUsageRecord(
        attempt_id=uuid4(),
        experiment_id="e1",
        task_id="t1",
        mode="c",
        turns=2,
        prompt_tokens=10,
        completion_tokens=5,
        reasoning_tokens=0,
        cache_hit_tokens=0,
        cache_miss_tokens=15,
        total_tokens=15,
        cost_usd=Decimal("0.001"),
        price_snapshot_id="snap",
        price_band="off_peak",
        first_recorded_at=now - timedelta(days=3),
        last_recorded_at=now - timedelta(days=3) + timedelta(seconds=5),
    )
    attempt = AttemptRow(
        attempt_id=uuid4(),
        experiment_id="e1",
        task_id="t1",
        mode="c",
        started_at=now - timedelta(minutes=1),
        finished_at=now,
    )
    assignment = assign_attempts([record], [attempt])
    assert assignment.assigned == {}
    assert len(assignment.unassigned) == 1


def test_codex_f10_cte_candidate_parses_and_policy_decides() -> None:
    arguments = {
        "type": "submit_sql_candidate",
        "evidence_refs": ["table.orders"],
        "sql": "WITH x AS (SELECT COUNT(*) AS n FROM retail.orders) SELECT n FROM x LIMIT 1",
        "step_id": "q.s1",
    }
    output = ToolCallOutput(
        type="tool_calls",
        tool_calls=[
            ToolCall(
                call_id="c1",
                name="submit_sql_candidate",
                arguments_json=json.dumps(arguments, sort_keys=True, separators=(",", ":")),
            )
        ],
    )
    sql, refs = parse_sql_candidate(output)
    assert "WITH" in sql
    assert refs == ("table.orders",)
    AstPolicy().validate(sql)


def test_codex_f3_sse_cursor_advances_by_run_cursor(monkeypatch) -> None:
    """The reconnect id must be the run cursor the source filters on, not the
    attempt-local sequence — otherwise the tail event re-serves forever."""
    from commerce_agent.api.events import SseRunEvent
    from commerce_agent.api.sse import sse_stream

    run_id = uuid4()

    def _event(cursor: int, sequence: int) -> SseRunEvent:
        return SseRunEvent(
            cursor=cursor,
            run_id=run_id,
            attempt_id=uuid4(),
            sequence=sequence,
            event_type="attempt_started",
            status="succeeded",
            reason_code=None,
            occurred_at=datetime.now(UTC),
        )

    class _Source:
        def __init__(self) -> None:
            self.queries: list[int] = []
            self._events = [_event(1, 0), _event(2, 1)]

        async def events_after(self, run_id, *, after_sequence: int, limit: int = 200):
            self.queries.append(after_sequence)
            return [e for e in self._events if e.cursor > after_sequence]

    source = _Source()

    async def _pull(last_event_id: int) -> tuple[int, int]:
        """One pull; returns (block id, query cursor) as the client would
        observe them — the block id is what the client saves."""
        generator = sse_stream(
            source, run_id, last_event_id=last_event_id,
            poll_interval=0.0, heartbeat_interval=999,
        )
        block = await asyncio.wait_for(generator.__anext__(), timeout=2)
        block_id = int(block.splitlines()[0].removeprefix("id: "))
        return block_id, source.queries[-1]

    # first pull from the beginning: serves the first event, queries -1
    block_id, _queried = asyncio.run(_pull(0))
    assert block_id == 1
    # second pull resumes from the SAVED id (=1): serves the tail event
    block_id, _queried = asyncio.run(_pull(1))
    assert block_id == 2
    # third pull resumes from the saved id (=2, the run cursor of the tail):
    # post-fix it re-serves nothing — pre-fix the id was the attempt-local
    # sequence (1), so this pull re-served the tail forever
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(_pull(2))


def test_codex_f3_same_generator_third_pull_does_not_replay_tail() -> None:
    """Codex round-2 P2-4: the F3 regression above creates a fresh generator
    per pull; the original defect lived INSIDE one generator's loop. Replay
    the exact scenario on a single generator: two events served, third pull
    must block instead of re-serving the tail."""
    from commerce_agent.api.events import SseRunEvent
    from commerce_agent.api.sse import sse_stream

    run_id = uuid4()

    def _event(cursor: int, sequence: int) -> SseRunEvent:
        return SseRunEvent(
            cursor=cursor,
            run_id=run_id,
            attempt_id=uuid4(),
            sequence=sequence,
            event_type="attempt_started",
            status="succeeded",
            reason_code=None,
            occurred_at=datetime.now(UTC),
        )

    class _Source:
        def __init__(self) -> None:
            self.queries: list[int] = []
            self._events = [_event(1, 0), _event(2, 1)]

        async def events_after(self, run_id, *, after_sequence: int, limit: int = 200):
            self.queries.append(after_sequence)
            return [e for e in self._events if e.cursor > after_sequence]

    async def _scenario() -> list[int]:
        generator = sse_stream(
            _Source(), run_id, last_event_id=0,
            poll_interval=0.0, heartbeat_interval=999,
        )
        served: list[int] = []
        for _ in range(2):
            block = await asyncio.wait_for(generator.__anext__(), timeout=2)
            served.append(int(block.splitlines()[0].removeprefix("id: ")))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(generator.__anext__(), timeout=0.2)
        return served

    assert asyncio.run(_scenario()) == [1, 2]


def test_codex_f5_cancelled_episode_kills_official_child(tmp_path, monkeypatch) -> None:
    """Codex F5 / round-2 P2-4: a cancelled episode must kill the official
    orchestrator subprocess — an abandoned live process keeps calling the
    paid model API. Fake process blocks in communicate(); cancel must
    trigger kill()+wait() and re-raise CancelledError."""
    import sys

    from commerce_agent.evaluation._official import OfficialOrchestratorEpisodeExecutor
    from commerce_agent.evaluation.contracts import AttemptRecord

    attempt = AttemptRecord(
        attempt_id=uuid4(),
        run_id=uuid4(),
        experiment_id="fx",
        task_id="t0",
        mode="c",
        attempt_seq=1,
        started_at=datetime.now(UTC),
    )
    executor = OfficialOrchestratorEpisodeExecutor(
        adk_root=tmp_path / "adk",
        python_executable=sys.executable,
        data_file_for=lambda task: tmp_path / "data.jsonl",
        output_dir=tmp_path / "episodes",
    )

    killed = {"kill": False, "wait": False}

    class _FakeProcess:
        returncode = None

        async def communicate(self):
            await asyncio.Event().wait()  # blocks forever until cancelled

        def kill(self):
            killed["kill"] = True

        async def wait(self):
            killed["wait"] = True
            return 0

    async def _fake_exec(*args, **kwargs):
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    async def _scenario() -> None:
        running = asyncio.ensure_future(
            executor.execute(EpisodeTask(task_id="t0", mode="c"), attempt)
        )
        await asyncio.sleep(0.05)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    asyncio.run(_scenario())
    assert killed["kill"] is True
    assert killed["wait"] is True
