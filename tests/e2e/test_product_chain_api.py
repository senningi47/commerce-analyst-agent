"""API-layer E2E: the workbench chain over REST + SSE against real PostgreSQL.

Day 6 Task 9 acceptance (§19.3 product-side items): an ambiguous business
question's full investigation chain - clarification, plan, SQL rejection and
repair, execution, reconciliation, two proposal revisions (rejection, then
approval), controlled execution with readback, and the report - flows through
the read-only API exactly as the audit wrote it; each of the three product
closed loops surfaces one approval, one execution, and one readback; and the
eval center readback serves real eval rows over REST. Pure read side: audit
fixtures are written through the same Postgres seams the closed loops use
(trace_writer / evaluation_writer) and consumed only through the
agent_reader identity, so the whitelisted views stay the privacy boundary.
"""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from commerce_agent.api.app import create_app
from commerce_agent.api.eval import PostgresEvalSource
from commerce_agent.api.runs import PostgresRunDirectory
from commerce_agent.api.sse import PostgresTraceEventSource, sse_stream
from commerce_agent.evaluation._postgres import PostgresEvaluationStore
from commerce_agent.evaluation.contracts import (
    AttemptRecord,
    AttemptTelemetry,
    EpisodeResult,
    EvalTaskStatus,
)
from commerce_agent.model.contracts import RunScope
from commerce_agent.product_eval._reset import PostgresScenarioReset
from commerce_agent.trace._postgres import PostgresTraceStore
from commerce_agent.trace.contracts import (
    ScopedTraceEvent,
    TraceDecisionSummary,
    TraceEventType,
    TraceStatus,
)

pytestmark = pytest.mark.postgres

FIXED_TIME = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)

SCENARIO_INVESTIGATION = "task15-e2e-investigation-v1"
SCENARIO_SELLER_RISK = "task15-e2e-seller-risk-v1"
SCENARIO_ALERT = "task15-e2e-alert-v1"
ALL_SCENARIOS = (SCENARIO_INVESTIGATION, SCENARIO_SELLER_RISK, SCENARIO_ALERT)

INVESTIGATION_SEQUENCE = (
    "clarification_requested",
    "investigation_plan_accepted",
    "sql_generated",
    "sql_repaired",
    "query_executed",
    "query_reconciled",
    "proposal_created",
    "decision_recorded",
    "proposal_created",
    "decision_recorded",
    "execution_completed",
    "report_completed",
    "recovery_applied",
)

# §18 wire surface: the SSE payload carries exactly these keys and no others.
PAYLOAD_WHITELIST = frozenset(
    {
        "cursor",
        "run_id",
        "attempt_id",
        "sequence",
        "event_type",
        "status",
        "occurred_at",
        "reason_code",
        "decision_summary",
        "proposal_ref",
        "execution_ref",
        "audit_ref",
        "evidence",
        "query_fingerprints",
        "usage",
        "cost",
    }
)


def _trace_event(
    scenario_id: str,
    run_id: UUID,
    attempt_id: UUID,
    sequence: int,
    event_type: TraceEventType,
    *,
    decision_summary: TraceDecisionSummary | None = None,
    proposal_ref: str | None = None,
    execution_ref: str | None = None,
    audit_ref: str | None = None,
    second_offset: int = 0,
) -> ScopedTraceEvent:
    return ScopedTraceEvent(
        run_scope=RunScope(
            run_id=run_id,
            track="retail",
            mode="retail",
            subject_id=scenario_id,
            experiment_id="day6-e2e",
            config_hash="c" * 64,
        ),
        attempt_id=attempt_id,
        phase="closed_loop",
        sequence=sequence,
        occurred_at=FIXED_TIME + timedelta(seconds=second_offset),
        node="e2e",
        event_type=event_type,
        status=TraceStatus.SUCCEEDED,
        decision_summary=decision_summary,
        config_hash="c" * 64,
        proposal_ref=proposal_ref,
        execution_ref=execution_ref,
        audit_ref=audit_ref,
    )


def _investigation_chain(scenario_id: str) -> tuple[UUID, list[ScopedTraceEvent]]:
    run_id = uuid4()
    attempt_one = uuid4()
    attempt_two = uuid4()
    proposal_one = f"proposal:{attempt_one}:1"
    proposal_two = f"proposal:{attempt_one}:2"
    events = [
        _trace_event(
            scenario_id, run_id, attempt_one, 0, TraceEventType.CLARIFICATION_REQUESTED
        ),
        _trace_event(
            scenario_id,
            run_id,
            attempt_one,
            1,
            TraceEventType.INVESTIGATION_PLAN_ACCEPTED,
            decision_summary=TraceDecisionSummary(
                code="plan_accepted",
                text="按收货地聚合上周 GMV，并与前一自然周对比。",
            ),
        ),
        _trace_event(
            scenario_id, run_id, attempt_one, 2, TraceEventType.SQL_GENERATED
        ),
        _trace_event(
            scenario_id, run_id, attempt_one, 3, TraceEventType.SQL_REPAIRED
        ),
        _trace_event(
            scenario_id, run_id, attempt_one, 4, TraceEventType.QUERY_EXECUTED
        ),
        _trace_event(
            scenario_id, run_id, attempt_one, 5, TraceEventType.QUERY_RECONCILED
        ),
        _trace_event(
            scenario_id,
            run_id,
            attempt_one,
            6,
            TraceEventType.PROPOSAL_CREATED,
            proposal_ref=proposal_one,
        ),
        _trace_event(
            scenario_id,
            run_id,
            attempt_one,
            7,
            TraceEventType.DECISION_RECORDED,
            decision_summary=TraceDecisionSummary(
                code="changes_requested",
                text="预算下调并把河北纳入范围后再提交。",
            ),
            proposal_ref=proposal_one,
        ),
        _trace_event(
            scenario_id,
            run_id,
            attempt_one,
            8,
            TraceEventType.PROPOSAL_CREATED,
            proposal_ref=proposal_two,
        ),
        _trace_event(
            scenario_id,
            run_id,
            attempt_one,
            9,
            TraceEventType.DECISION_RECORDED,
            decision_summary=TraceDecisionSummary(
                code="proposal_approved",
                text="批准修订版提案。",
            ),
            proposal_ref=proposal_two,
        ),
        _trace_event(
            scenario_id,
            run_id,
            attempt_one,
            10,
            TraceEventType.EXECUTION_COMPLETED,
            execution_ref=f"execution:{attempt_one}",
            audit_ref=f"audit:{attempt_one}",
        ),
        _trace_event(
            scenario_id,
            run_id,
            attempt_one,
            11,
            TraceEventType.REPORT_COMPLETED,
            decision_summary=TraceDecisionSummary(
                code="report_ready",
                text="上周华北 GMV 环比下降 9.8%，已批准价格复审并执行读回一致。",
            ),
        ),
        _trace_event(
            scenario_id,
            run_id,
            attempt_two,
            0,
            TraceEventType.RECOVERY_APPLIED,
            second_offset=100,
        ),
    ]
    return run_id, events


def _operation_loop(scenario_id: str, label: str) -> tuple[UUID, list[ScopedTraceEvent]]:
    """One approval + execution + readback for the seller-risk / alert loops."""
    run_id = uuid4()
    attempt_id = uuid4()
    proposal_ref = f"proposal:{attempt_id}:1"
    events = [
        _trace_event(
            scenario_id,
            run_id,
            attempt_id,
            0,
            TraceEventType.PROPOSAL_CREATED,
            proposal_ref=proposal_ref,
        ),
        _trace_event(
            scenario_id,
            run_id,
            attempt_id,
            1,
            TraceEventType.DECISION_RECORDED,
            decision_summary=TraceDecisionSummary(
                code="proposal_approved",
                text=f"批准{label}提案。",
            ),
            proposal_ref=proposal_ref,
        ),
        _trace_event(
            scenario_id,
            run_id,
            attempt_id,
            2,
            TraceEventType.EXECUTION_COMPLETED,
            execution_ref=f"execution:{attempt_id}",
            audit_ref=f"audit:{attempt_id}",
        ),
    ]
    return run_id, events


async def _write_audit(scenario_id: str, events: list[ScopedTraceEvent]) -> None:
    store = PostgresTraceStore(
        SecretStr(os.environ["PRODUCT_TRACE_DATABASE_DSN"]), scenario_id=scenario_id
    )
    for event in events:
        await store.append(event)


async def _take_blocks(source: PostgresTraceEventSource, run_id: UUID, count: int):
    blocks: list[str] = []
    async for chunk in sse_stream(
        source,
        run_id,
        last_event_id=-1,
        poll_interval=0.05,
        heartbeat_interval=3600.0,
    ):
        blocks.append(chunk)
        if len(blocks) >= count:
            break
    return blocks


def _data_of(block: str) -> str:
    return next(line[6:] for line in block.splitlines() if line.startswith("data: "))


def _event_type_of(block: str) -> str:
    return next(
        line[7:] for line in block.splitlines() if line.startswith("event: ")
    )


def _read_app():
    read_dsn = SecretStr(os.environ["PRODUCT_DATABASE_DSN"])
    return create_app(
        event_source=PostgresTraceEventSource(read_dsn),
        eval_source=PostgresEvalSource(read_dsn),
        run_directory=PostgresRunDirectory(read_dsn),
    )


@pytest.fixture
async def clean_loops() -> dict[str, UUID]:
    """Reset three scoped scenarios, write the three loop audits, yield run ids."""
    reset = PostgresScenarioReset(
        SecretStr(os.environ["PRODUCT_SCENARIO_RESET_DATABASE_DSN"])
    )
    for scenario in ALL_SCENARIOS:
        await reset.reset(scenario, "scenario-reset-v1")
    investigation_run, investigation = _investigation_chain(SCENARIO_INVESTIGATION)
    seller_run, seller = _operation_loop(SCENARIO_SELLER_RISK, "卖家风险处置")
    alert_run, alert = _operation_loop(SCENARIO_ALERT, "指标预警规则启用")
    await _write_audit(SCENARIO_INVESTIGATION, investigation)
    await _write_audit(SCENARIO_SELLER_RISK, seller)
    await _write_audit(SCENARIO_ALERT, alert)
    try:
        yield {
            "investigation": investigation_run,
            "seller_risk": seller_run,
            "alert": alert_run,
        }
    finally:
        for scenario in ALL_SCENARIOS:
            await reset.reset(scenario, "scenario-reset-v1")


@pytest.fixture
async def seeded_eval() -> str:
    experiment_id = f"live-e2e-eval-{uuid.uuid4().hex[:12]}"
    store = PostgresEvaluationStore(dsn=os.environ["PRODUCT_EVALUATION_DATABASE_DSN"])
    store.register_experiment(
        experiment_id=experiment_id, purpose="product", config_hash="d" * 64
    )
    record = AttemptRecord(
        attempt_id=uuid4(),
        run_id=uuid4(),
        experiment_id=experiment_id,
        task_id="retail_gmv_investigation",
        mode="c",
        attempt_seq=1,
        status=EvalTaskStatus.PENDING,
        started_at=FIXED_TIME,
    )
    store.register_attempt(record)
    store.mark_running(record.attempt_id, expected_status=EvalTaskStatus.PENDING)
    store.finish_attempt(
        record.attempt_id,
        status=EvalTaskStatus.SUCCEEDED,
        error_class=None,
        telemetry=AttemptTelemetry(agent_turns=9),
    )
    store.record_result(
        record.attempt_id,
        EpisodeResult(
            reward=Decimal(0),
            phase1_passed=False,
            rounds=6,
            submit_count=1,
        ),
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
async def test_workbench_chain_flows_through_rest_and_sse(
    clean_loops: dict[str, UUID],
) -> None:
    client = TestClient(_read_app())
    runs = client.get("/api/runs").json()
    row = next(
        item
        for item in runs
        if item["run_id"] == str(clean_loops["investigation"])
    )
    assert row["event_count"] == 13

    source = PostgresTraceEventSource(
        SecretStr(os.environ["PRODUCT_DATABASE_DSN"])
    )
    blocks = await _take_blocks(
        source, clean_loops["investigation"], len(INVESTIGATION_SEQUENCE)
    )
    assert [_event_type_of(block) for block in blocks] == list(
        INVESTIGATION_SEQUENCE
    )
    ids = [
        int(next(line for line in block.splitlines() if line.startswith("id: "))[4:])
        for block in blocks
    ]
    assert ids == list(range(1, 14))

    rejected = json.loads(_data_of(blocks[7]))
    assert rejected["decision_summary"]["code"] == "changes_requested"
    approved = json.loads(_data_of(blocks[9]))
    assert approved["decision_summary"]["code"] == "proposal_approved"
    execution = json.loads(_data_of(blocks[10]))
    assert execution["execution_ref"] and execution["audit_ref"]
    report = json.loads(_data_of(blocks[11]))
    assert report["decision_summary"]["code"] == "report_ready"

    for block in blocks:
        payload = json.loads(_data_of(block))
        assert set(payload) <= PAYLOAD_WHITELIST
        assert "node" not in payload and "phase" not in payload


@pytest.mark.asyncio
async def test_three_closed_loops_each_surface_approval_execution_readback(
    clean_loops: dict[str, UUID],
) -> None:
    client = TestClient(_read_app())
    runs = {item["run_id"]: item for item in client.get("/api/runs").json()}
    source = PostgresTraceEventSource(
        SecretStr(os.environ["PRODUCT_DATABASE_DSN"])
    )
    for key in ("seller_risk", "alert"):
        run_id = clean_loops[key]
        assert runs[str(run_id)]["event_count"] == 3
        blocks = await _take_blocks(source, run_id, 3)
        assert [_event_type_of(block) for block in blocks] == [
            "proposal_created",
            "decision_recorded",
            "execution_completed",
        ]
        approval = json.loads(_data_of(blocks[1]))
        assert approval["decision_summary"]["code"] == "proposal_approved"
        execution = json.loads(_data_of(blocks[2]))
        assert execution["execution_ref"]
        assert execution["audit_ref"]


@pytest.mark.asyncio
async def test_eval_center_readback_serves_rows_over_rest(
    seeded_eval: str,
) -> None:
    client = TestClient(_read_app())
    experiments = client.get("/api/eval/experiments").json()
    summary = next(
        item for item in experiments if item["experiment_id"] == seeded_eval
    )
    assert summary["purpose"] == "product"
    assert summary["status_counts"]["succeeded"] == 1
    attempts = client.get(
        f"/api/eval/experiments/{seeded_eval}/attempts"
    ).json()
    assert len(attempts) == 1
    assert float(attempts[0]["reward"]) == 0.0
    assert attempts[0]["phase1_passed"] is False
    assert attempts[0]["agent_turns"] == 9
