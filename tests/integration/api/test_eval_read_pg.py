"""PG gate: the read-only eval/run-directory API serves real rows as agent_reader.

Day 6 Task 8 Step 2/3 acceptance: register a scoped experiment through the
evaluation store, then read it back through the 0007 ``ops_read`` views with
the ``agent_reader`` identity only - the whitelist view IS the privacy
boundary, and the raw eval tables must stay unreadable to the API identity.
"""

import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from pydantic import SecretStr

from commerce_agent.api.eval import PostgresEvalSource
from commerce_agent.api.runs import PostgresRunDirectory
from commerce_agent.evaluation._postgres import PostgresEvaluationStore
from commerce_agent.evaluation.contracts import (
    AttemptRecord,
    AttemptTelemetry,
    EpisodeResult,
    EvalTaskStatus,
)
from commerce_agent.model.contracts import CostEstimate, RunScope
from commerce_agent.trace._postgres import PostgresTraceStore
from commerce_agent.trace.contracts import ScopedTraceEvent, TraceEventType, TraceStatus

pytestmark = pytest.mark.postgres

FIXED_TIME = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)
AGENT_COST = Decimal("0.0177")
SIM_COST = Decimal("0.03144")
EXPERIMENT_COLUMNS = [
    "experiment_id",
    "purpose",
    "config_hash",
    "created_at",
    "closed_at",
]
ATTEMPT_COLUMNS = [
    "attempt_id",
    "run_id",
    "experiment_id",
    "task_id",
    "mode",
    "attempt_seq",
    "status",
    "error_class",
    "started_at",
    "finished_at",
    "reward",
    "phase1_passed",
    "phase2_passed",
    "rounds",
    "tool_calls",
    "submit_count",
    "agent_cost_amount",
    "simulator_cost_amount",
    "agent_turns",
]


def _record(experiment_id: str, mode: str, seq: int) -> AttemptRecord:
    return AttemptRecord(
        attempt_id=uuid4(),
        run_id=uuid4(),
        experiment_id=experiment_id,
        task_id="cybermarket_pattern_12",
        mode=mode,
        attempt_seq=seq,
        status=EvalTaskStatus.PENDING,
        started_at=FIXED_TIME,
    )


@pytest.fixture
async def seeded_experiment() -> str:
    experiment_id = f"live-eval-read-{uuid.uuid4().hex[:12]}"
    store = PostgresEvaluationStore(dsn=os.environ["PRODUCT_EVALUATION_DATABASE_DSN"])
    store.register_experiment(
        experiment_id=experiment_id, purpose="pilot", config_hash="c" * 64
    )
    succeeded = _record(experiment_id, "a", 1)
    store.register_attempt(succeeded)
    store.mark_running(succeeded.attempt_id, expected_status=EvalTaskStatus.PENDING)
    store.finish_attempt(
        succeeded.attempt_id,
        status=EvalTaskStatus.SUCCEEDED,
        error_class=None,
        telemetry=AttemptTelemetry(
            agent_cost=CostEstimate(
                status="estimated",
                price_snapshot_id="deepseek-flash-price-2026-09-12",
                currency="USD",
                amount=AGENT_COST,
                price_band="off_peak",
            ),
            simulator_cost=CostEstimate(
                status="estimated",
                price_snapshot_id="deepseek-flash-price-2026-09-12",
                currency="USD",
                amount=SIM_COST,
                price_band="off_peak",
            ),
            agent_turns=18,
        ),
    )
    store.record_result(
        succeeded.attempt_id,
        EpisodeResult(
            reward=Decimal(0),
            phase1_passed=False,
            rounds=4,
            tool_calls=7,
            submit_count=1,
        ),
    )
    interrupted = _record(experiment_id, "c", 1)
    store.register_attempt(interrupted)
    store.mark_running(interrupted.attempt_id, expected_status=EvalTaskStatus.PENDING)
    store.finish_attempt(
        interrupted.attempt_id,
        status=EvalTaskStatus.INTERRUPTED,
        error_class=None,
        telemetry=AttemptTelemetry(),
    )
    try:
        yield experiment_id
    finally:
        admin_dsn = os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "DELETE FROM eval.task_result WHERE attempt_id IN "
                "(SELECT attempt_id FROM eval.task_attempt WHERE experiment_id = %s)",
                (experiment_id,),
            )
            connection.execute(
                "DELETE FROM eval.task_attempt WHERE experiment_id = %s",
                (experiment_id,),
            )
            connection.execute(
                "DELETE FROM eval.experiment WHERE experiment_id = %s",
                (experiment_id,),
            )


@pytest.mark.asyncio
async def test_experiment_summary_serves_status_counts_and_costs(
    seeded_experiment: str,
) -> None:
    source = PostgresEvalSource(SecretStr(os.environ["PRODUCT_DATABASE_DSN"]))
    summaries = await source.experiments()
    summary = next(
        item for item in summaries if item.experiment_id == seeded_experiment
    )
    assert summary.purpose == "pilot"
    assert summary.attempt_total == 2
    assert summary.status_counts == {
        "pending": 0,
        "running": 0,
        "succeeded": 1,
        "failed": 0,
        "infrastructure_error": 0,
        "interrupted": 1,
    }
    assert summary.reward_total == Decimal(0)
    assert summary.agent_cost_total == AGENT_COST


@pytest.mark.asyncio
async def test_attempt_rows_join_results_and_extract_telemetry(
    seeded_experiment: str,
) -> None:
    source = PostgresEvalSource(SecretStr(os.environ["PRODUCT_DATABASE_DSN"]))
    records = await source.attempts(seeded_experiment)
    assert len(records) == 2
    succeeded = next(item for item in records if item.status == "succeeded")
    assert succeeded.reward == Decimal(0)
    assert succeeded.phase1_passed is False
    assert succeeded.submit_count == 1
    assert succeeded.agent_cost_amount == AGENT_COST
    assert succeeded.simulator_cost_amount == SIM_COST
    assert succeeded.agent_turns == 18
    interrupted = next(item for item in records if item.status == "interrupted")
    assert interrupted.reward is None
    assert interrupted.agent_cost_amount is None
    assert interrupted.agent_turns is None


@pytest.mark.asyncio
async def test_view_columns_are_exactly_the_public_whitelist() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_DATABASE_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'ops_read' AND table_name IN "
            "('eval_experiments', 'eval_attempts') "
            "ORDER BY table_name, ordinal_position"
        )
        rows = await cursor.fetchall()
    finally:
        await connection.close()
    columns: dict[str, list[str]] = {}
    for table_name, column_name in rows:
        columns.setdefault(table_name, []).append(column_name)
    assert columns["eval_experiments"] == EXPERIMENT_COLUMNS
    assert columns["eval_attempts"] == ATTEMPT_COLUMNS


@pytest.mark.asyncio
async def test_agent_reader_cannot_read_raw_eval_tables() -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_DATABASE_DSN"]
    )
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await connection.execute("SELECT count(*) FROM eval.task_attempt")
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_run_directory_lists_a_written_run() -> None:
    scenario_id = "task15-api-eval-read-v1"
    run_id = uuid4()
    reset_dsn = SecretStr(os.environ["PRODUCT_SCENARIO_RESET_DATABASE_DSN"])
    from commerce_agent.product_eval._reset import PostgresScenarioReset

    reset = PostgresScenarioReset(reset_dsn)
    await reset.reset(scenario_id, "scenario-reset-v1")
    store = PostgresTraceStore(
        SecretStr(os.environ["PRODUCT_TRACE_DATABASE_DSN"]), scenario_id=scenario_id
    )
    await store.append(
        ScopedTraceEvent(
            run_scope=RunScope(
                run_id=run_id,
                track="retail",
                mode="retail",
                subject_id=scenario_id,
                experiment_id="day6-api-eval-read",
                config_hash="c" * 64,
            ),
            attempt_id=uuid4(),
            phase="closed_loop",
            sequence=0,
            occurred_at=FIXED_TIME,
            node="clarify",
            event_type=TraceEventType.CLARIFICATION_REQUESTED,
            status=TraceStatus.SUCCEEDED,
            config_hash="c" * 64,
        )
    )
    try:
        directory = PostgresRunDirectory(
            SecretStr(os.environ["PRODUCT_DATABASE_DSN"])
        )
        summaries = await directory.runs()
        summary = next(item for item in summaries if item.run_id == run_id)
        assert summary.event_count == 1
        assert summary.started_at == FIXED_TIME
        assert summary.last_event_at == FIXED_TIME
    finally:
        await reset.reset(scenario_id, "scenario-reset-v1")
