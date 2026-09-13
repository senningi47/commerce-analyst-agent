"""Live PostgreSQL test for the Day 6 spool-import telemetry merge.

Gated behind `-m postgres` + COMMERCE_AGENT_RUN_POSTGRES_TESTS=1 (a default
skip is not evidence). Verifies that `merge_telemetry` jsonb-merges the
agent-side usage/cost patch into existing telemetry without clobbering the
runner-written `wall_clock_ms`, and that the merged telemetry validates
against the `AttemptTelemetry` contract on read-back.
"""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from commerce_agent.evaluation._postgres import PostgresEvaluationStore
from commerce_agent.evaluation.contracts import (
    AttemptRecord,
    AttemptTelemetry,
    EvalTaskStatus,
)
from commerce_agent.evaluation.spool_importer import (
    AttemptRow,
    assign_attempts,
    scan_spool_dir,
    telemetry_patch,
)

pytestmark = pytest.mark.postgres

FIXED_START = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)


@pytest.fixture
def evaluation_store() -> PostgresEvaluationStore:
    return PostgresEvaluationStore(dsn=os.environ["PRODUCT_EVALUATION_DATABASE_DSN"])


@pytest.fixture
def scoped_experiment(evaluation_store: PostgresEvaluationStore):
    """Unique experiment with admin-DSN cleanup (least-privilege writer never DELETEs)."""
    experiment_id = f"live-spool-import-{uuid.uuid4().hex[:12]}"
    admin_dsn = os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    evaluation_store.register_experiment(
        experiment_id=experiment_id, purpose="pilot", config_hash="a" * 64
    )
    try:
        yield experiment_id
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            cursor = connection.execute(
                "DELETE FROM eval.task_result WHERE attempt_id IN "
                "(SELECT attempt_id FROM eval.task_attempt WHERE experiment_id = %s)",
                (experiment_id,),
            )
            cursor = connection.execute(
                "DELETE FROM eval.task_attempt WHERE experiment_id = %s",
                (experiment_id,),
            )
            cursor = connection.execute(
                "DELETE FROM eval.experiment WHERE experiment_id = %s",
                (experiment_id,),
            )
            assert cursor.rowcount == 1


def make_record(experiment_id: str, task_id: str, mode: str) -> AttemptRecord:
    return AttemptRecord(
        attempt_id=uuid4(),
        run_id=uuid4(),
        experiment_id=experiment_id,
        task_id=task_id,
        mode=mode,
        attempt_seq=1,
        status=EvalTaskStatus.SUCCEEDED,
        started_at=FIXED_START,
        finished_at=FIXED_START + timedelta(seconds=60),
        telemetry=AttemptTelemetry(wall_clock_ms=77141),
    )


def _write_spool(path: Path, *, task_id: str, mode: str, experiment_id: str) -> None:
    events = []
    for sequence in range(2):
        events.append(
            {
                "run_scope_digest": "a" * 64,
                "attempt_id": str(path.stem),
                "phase": "attempt",
                "sequence": sequence,
                "event_type": "model_turn",
                "task_id": task_id,
                "mode": mode,
                "experiment_id": experiment_id,
                "recorded_at": "2026-09-14T08:00:30+00:00",
                "payload": {
                    "actual_model": "deepseek-flash",
                    "finish_reason": "tool_calls",
                    "usage": {
                        "prompt_tokens": 550,
                        "completion_tokens": 120,
                        "reasoning_tokens": 40,
                        "cache_hit_tokens": 10,
                        "cache_miss_tokens": 540,
                        "total_tokens": 670,
                    },
                    "cost": {
                        "amount": "0.000166",
                        "currency": "USD",
                        "price_band": "off_peak",
                        "price_snapshot_id": "deepseek-flash-usd-2026-09-12",
                    },
                },
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )


def test_merge_telemetry_preserves_wall_clock(
    evaluation_store: PostgresEvaluationStore, scoped_experiment: str, tmp_path: Path
) -> None:
    task_id = f"spool-task-{uuid.uuid4().hex[:8]}"
    record = make_record(scoped_experiment, task_id, "a")
    evaluation_store.register_attempt(record)

    _write_spool(
        tmp_path / f"{uuid4()}.jsonl",
        task_id=task_id,
        mode="a",
        experiment_id=scoped_experiment,
    )

    report = scan_spool_dir(tmp_path)
    assert len(report.records) == 1
    assert report.records[0].turns == 2

    attempt_row = AttemptRow(
        attempt_id=record.attempt_id,
        experiment_id=scoped_experiment,
        task_id=task_id,
        mode="a",
        started_at=record.started_at,
        finished_at=record.finished_at,
    )
    assignment = assign_attempts(list(report.records), [attempt_row])
    assert list(assignment.assigned.keys()) == [record.attempt_id]
    assert assignment.unassigned == ()
    assert assignment.ambiguous == ()

    patch = telemetry_patch(assignment.assigned[record.attempt_id])
    evaluation_store.merge_telemetry(record.attempt_id, patch)

    with psycopg.connect(
        os.environ["PRODUCT_EVALUATION_DATABASE_DSN"]
    ) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT telemetry FROM eval.task_attempt WHERE attempt_id = %s",
            (record.attempt_id,),
        )
        (telemetry_json,) = cursor.fetchone()

    merged = AttemptTelemetry.model_validate(telemetry_json)
    assert merged.wall_clock_ms == 77141  # runner-written value survived the merge
    assert merged.agent_cost is not None
    assert merged.agent_cost.amount == Decimal("0.000332")
    assert merged.agent_usage is not None
    assert merged.agent_usage.prompt_tokens == 1100
    assert merged.agent_usage.prompt_tokens == (
        merged.agent_usage.cache_hit_tokens + merged.agent_usage.cache_miss_tokens
    )
    assert merged.agent_turns == 2
