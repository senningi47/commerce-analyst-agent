"""Seed one temporary demo run for the UI live-mode browser check.

Writes a handful of REAL trace events through PostgresTraceStore (the same
seam the Task 7 SSE gate drives) under a scoped scenario id, then the caller
resets the scenario to restore the clean state. Zero paid calls, zero GT.

Run: ``uv run --env-file .env python scripts/seed_ui_live_run.py``
"""

import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from pydantic import SecretStr

from commerce_agent.model.contracts import CostEstimate, RunScope
from commerce_agent.trace._postgres import PostgresTraceStore
from commerce_agent.trace.contracts import (
    ScopedTraceEvent,
    TraceDecisionSummary,
    TraceEventType,
    TraceEvidence,
    TraceStatus,
)

SCENARIO_ID = "day6-ui-live-v1"
RUN_ID = uuid4()
ATTEMPT_ID = uuid4()
NOW = datetime.now(UTC)

SEQUENCE: list[tuple[TraceEventType, TraceDecisionSummary | None]] = [
    (TraceEventType.CLARIFICATION_REQUESTED, None),
    (
        TraceEventType.INVESTIGATION_PLAN_ACCEPTED,
        TraceDecisionSummary(
            code="plan_accepted",
            text="按收货地聚合上周 GMV，并与前一自然周对比。",
        ),
    ),
    (TraceEventType.SQL_GENERATED, None),
    (TraceEventType.SQL_REPAIRED, None),
    (TraceEventType.QUERY_EXECUTED, None),
    (
        TraceEventType.QUERY_RECONCILED,
        TraceDecisionSummary(
            code="reconciled",
            text="分省求和与订单宽表总额一致（667,400）。",
        ),
    ),
    (TraceEventType.PROPOSAL_CREATED, None),
    (
        TraceEventType.DECISION_RECORDED,
        TraceDecisionSummary(
            code="proposal_approved",
            text="批准定向促销提案，按预算执行。",
        ),
    ),
    (TraceEventType.EXECUTION_COMPLETED, None),
    (
        TraceEventType.REPORT_COMPLETED,
        TraceDecisionSummary(
            code="report_ready",
            text="上周华北 GMV 667,400 元，环比下降 9.8%；已批准价格复审并执行读回一致。",
        ),
    ),
]


def _event(sequence: int, event_type: TraceEventType, summary) -> ScopedTraceEvent:
    return ScopedTraceEvent(
        run_scope=RunScope(
            run_id=RUN_ID,
            track="retail",
            mode="retail",
            subject_id=SCENARIO_ID,
            experiment_id="day6-ui-live",
            config_hash="c" * 64,
        ),
        attempt_id=ATTEMPT_ID,
        phase="closed_loop",
        sequence=sequence,
        occurred_at=NOW,
        node="ui_live_seed",
        event_type=event_type,
        status=TraceStatus.SUCCEEDED,
        decision_summary=summary,
        config_hash="c" * 64,
        proposal_ref=(
            "proposal:00000000-0000-0000-0000-00000000d601:1"
            if event_type is TraceEventType.PROPOSAL_CREATED
            else None
        ),
        execution_ref=(
            "execution:00000000-0000-0000-0000-00000000d602"
            if event_type is TraceEventType.EXECUTION_COMPLETED
            else None
        ),
        evidence=(
            TraceEvidence(
                kind="knowledge",
                ref="metric:gmv",
                digest="a" * 64,
            ),
        )
        if sequence == 0
        else (),
        cost=(
            CostEstimate(
                status="estimated",
                price_snapshot_id="deepseek-flash-price-2026-09-12",
                currency="USD",
                amount=Decimal("0.000112"),
                price_band="off_peak",
            )
        )
        if event_type is TraceEventType.QUERY_EXECUTED
        else None,
    )


async def main() -> None:
    store = PostgresTraceStore(
        SecretStr(os.environ["PRODUCT_TRACE_DATABASE_DSN"]),
        scenario_id=SCENARIO_ID,
    )
    for sequence, (event_type, summary) in enumerate(SEQUENCE):
        await store.append(_event(sequence, event_type, summary))
    print(f"seeded run_id={RUN_ID} attempt_id={ATTEMPT_ID} events={len(SEQUENCE)}")


if __name__ == "__main__":
    # psycopg async refuses the Windows Proactor loop (see scripts/run_api.py).
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main())
