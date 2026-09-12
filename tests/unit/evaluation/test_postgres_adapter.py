import json
import threading
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Self
from uuid import uuid4

import pytest

from commerce_agent.evaluation._postgres import PostgresEvaluationStore
from commerce_agent.evaluation.contracts import (
    AttemptRecord,
    AttemptTelemetry,
    EpisodeResult,
    EvalStateConflict,
    EvalTaskStatus,
)

FIXED_TIME = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)


class FakeCursor:
    def __init__(self, *, rowcount: int = 1, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rowcount = rowcount
        self.rows = rows or []
        self.statements: list[tuple[str, tuple[Any, ...] | None]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: object, params: tuple[Any, ...] | None = None) -> None:
        self.statements.append((str(statement), params))

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


def make_store(cursor: FakeCursor) -> PostgresEvaluationStore:
    store = PostgresEvaluationStore(dsn="postgresql://evaluation_writer@127.0.0.1/db")
    real_connect = "psycopg.connect"

    def fake_connect(*args: object, **kwargs: object) -> FakeConnection:
        return FakeConnection(cursor)

    store.__dict__["_original_connect"] = real_connect  # documentation anchor
    import commerce_agent.evaluation._postgres as module

    module.psycopg.connect = fake_connect  # type: ignore[method-assign]
    return store


def make_record() -> AttemptRecord:
    return AttemptRecord(
        attempt_id=uuid4(),
        run_id=uuid4(),
        experiment_id="pilot-day5",
        task_id="task-1",
        mode="c",
        attempt_seq=1,
        status=EvalTaskStatus.PENDING,
        started_at=FIXED_TIME,
    )


def test_empty_dsn_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        PostgresEvaluationStore(dsn="")


def test_register_attempt_binds_all_columns_and_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(rowcount=1)
    store = make_store(cursor)
    record = make_record()

    store.register_attempt(record)

    statement, params = cursor.statements[0]
    assert "INSERT INTO eval.task_attempt" in statement
    assert params is not None
    assert params[0] == record.attempt_id
    assert params[4] == "c"
    assert params[5] == 1
    assert params[6] == "pending"
    assert params[9].obj == json.loads(AttemptTelemetry().model_dump_json())


def test_register_attempt_maps_unique_violation_to_conflict() -> None:
    import psycopg

    cursor = FakeCursor(rowcount=1)
    store = PostgresEvaluationStore(dsn="postgresql://evaluation_writer@127.0.0.1/db")

    def raising_connect(*args: object, **kwargs: object) -> FakeConnection:
        connection = FakeConnection(cursor)

        def broken_execute(statement: object, params: object = None) -> None:
            raise psycopg.errors.UniqueViolation()

        connection.cursor().execute = broken_execute  # type: ignore[method-assign]
        return connection

    psycopg.connect = raising_connect  # type: ignore[method-assign]
    try:
        with pytest.raises(EvalStateConflict) as excinfo:
            store.register_attempt(make_record())
    finally:
        import importlib

        importlib.reload(psycopg)
    assert excinfo.value.reason_code == "attempt_identity_conflict"


def test_mark_running_guard_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor(rowcount=0)
    store = make_store(cursor)

    with pytest.raises(EvalStateConflict) as excinfo:
        store.mark_running(uuid4(), expected_status=EvalTaskStatus.PENDING)

    assert excinfo.value.reason_code == "status_transition_conflict"
    statement, params = cursor.statements[0]
    assert "SET status = 'running'" in statement
    assert "AND status = 'pending'" in statement
    assert params == (cursor.statements[0][1][0],)


def test_mark_running_rejects_non_pending_expected_status() -> None:
    store = PostgresEvaluationStore(dsn="postgresql://evaluation_writer@127.0.0.1/db")
    with pytest.raises(EvalStateConflict) as excinfo:
        store.mark_running(uuid4(), expected_status=EvalTaskStatus.INTERRUPTED)
    assert excinfo.value.reason_code == "status_transition_conflict"


def test_finish_attempt_writes_terminal_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor(rowcount=1)
    store = make_store(cursor)
    attempt_id = uuid4()

    store.finish_attempt(
        attempt_id,
        status=EvalTaskStatus.FAILED,
        error_class="evaluator_rejected",
        telemetry=AttemptTelemetry(wall_clock_ms=2500),
    )

    statement, params = cursor.statements[0]
    assert "finished_at = now()" in statement
    assert "WHERE attempt_id = %s AND status = 'running'" in statement
    assert params is not None
    assert params[0] == "failed"
    assert params[1] == "evaluator_rejected"
    assert params[2].obj["wall_clock_ms"] == 2500


def test_completed_tasks_queries_terminal_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(rows=[("task-1", "c"), ("task-2", "a")])
    store = make_store(cursor)

    completed = store.completed_tasks("pilot-day5")

    assert completed == {("task-1", "c"), ("task-2", "a")}
    statement, params = cursor.statements[0]
    assert "status IN ('succeeded','failed')" in statement
    assert params == ("pilot-day5",)


def test_unfinished_attempts_returns_latest_non_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt_id = uuid4()
    run_id = uuid4()
    started = FIXED_TIME
    cursor = FakeCursor(
        rows=[
            (
                attempt_id,
                run_id,
                "pilot-day5",
                "task-3",
                "c",
                2,
                "interrupted",
                None,
                started,
                None,
                {"wall_clock_ms": 100},
            )
        ]
    )
    store = make_store(cursor)

    records = store.unfinished_attempts("pilot-day5")

    assert len(records) == 1
    record = records[0]
    assert record.attempt_id == attempt_id
    assert record.task_id == "task-3"
    assert record.status == EvalTaskStatus.INTERRUPTED
    assert record.telemetry.wall_clock_ms == 100
    statement, _params = cursor.statements[0]
    assert "DISTINCT ON (task_id, mode)" in statement
    assert "NOT EXISTS" in statement
    assert "status IN ('succeeded','failed')" in statement
    assert "attempt_seq DESC" in statement


def test_record_result_insert_select_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor(rowcount=1)
    store = make_store(cursor)
    attempt_id = uuid4()

    store.record_result(
        attempt_id,
        EpisodeResult(
            reward=Decimal("0.5"),
            phase1_passed=True,
            phase2_passed=False,
            rounds=2,
            tool_calls=5,
            submit_count=1,
        ),
    )

    statement, params = cursor.statements[0]
    assert "INSERT INTO eval.task_result" in statement
    assert "FROM eval.task_attempt" in statement
    assert "WHERE attempt_id = %s AND status = 'succeeded'" in statement
    assert "ON CONFLICT (attempt_id) DO NOTHING" in statement
    assert params is not None
    assert params[0] == attempt_id
    assert params[1] == Decimal("0.5")
    assert params[7] == attempt_id


def test_record_result_conflicts_when_guard_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(rowcount=0)
    store = make_store(cursor)

    with pytest.raises(EvalStateConflict) as excinfo:
        store.record_result(uuid4(), EpisodeResult())

    assert excinfo.value.reason_code == "result_requires_succeeded_attempt"


def test_store_operations_remain_callable_across_threads() -> None:
    """The adapter opens one connection per operation; no shared state leaks."""

    store = PostgresEvaluationStore(dsn="postgresql://evaluation_writer@127.0.0.1/db")
    cursor = FakeCursor(rowcount=1)

    import commerce_agent.evaluation._postgres as module

    def fake_connect(*args: object, **kwargs: object) -> FakeConnection:
        return FakeConnection(cursor)

    module.psycopg.connect = fake_connect  # type: ignore[method-assign]

    errors: list[Exception] = []

    def worker() -> None:
        try:
            store.completed_tasks("pilot-day5")
        except Exception as error:  # noqa: BLE001 -- collecting worker failures
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
