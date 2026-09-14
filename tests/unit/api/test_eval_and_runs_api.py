"""Read-only eval/run-directory API: row mapping, routing, and wiring."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from commerce_agent.api.app import create_app
from commerce_agent.api.eval import EvalAttemptRecord, _attempt_from_row, _summary_from_row
from commerce_agent.api.runs import RunSummary

EXPERIMENT_ROW = (
    "pilot-day5-20260913d",
    "pilot",
    "b" * 64,
    datetime(2026, 9, 13, 8, 0, tzinfo=UTC),
    None,
    20,
    0,
    0,
    18,
    1,
    0,
    1,
    0,
    Decimal("0.211203192"),
)


class FakeEvalSource:
    def __init__(self, experiments=(), attempts=()) -> None:
        self._experiments = tuple(experiments)
        self._attempts = tuple(attempts)
        self.requested: list[str] = []

    async def experiments(self) -> tuple:
        return self._experiments

    async def attempts(self, experiment_id: str) -> tuple:
        self.requested.append(experiment_id)
        return self._attempts


class FakeRunDirectory:
    def __init__(self, runs=()) -> None:
        self._runs = tuple(runs)

    async def runs(self) -> tuple:
        return self._runs


def test_attempt_row_maps_to_record() -> None:
    attempt_id = uuid4()
    run_id = uuid4()
    row = (
        attempt_id,
        run_id,
        "pilot-day5-20260913d",
        "cybermarket_pattern_12",
        "a",
        2,
        "interrupted",
        "official_process_failed",
        datetime(2026, 9, 13, 8, 20, tzinfo=UTC),
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        Decimal("0.0177"),
        None,
        18,
    )
    record = _attempt_from_row(row)
    assert record == EvalAttemptRecord(
        attempt_id=attempt_id,
        run_id=run_id,
        experiment_id="pilot-day5-20260913d",
        task_id="cybermarket_pattern_12",
        mode="a",
        attempt_seq=2,
        status="interrupted",
        error_class="official_process_failed",
        started_at=datetime(2026, 9, 13, 8, 20, tzinfo=UTC),
        finished_at=None,
        reward=None,
        phase1_passed=None,
        phase2_passed=None,
        rounds=None,
        tool_calls=None,
        submit_count=None,
        agent_cost_amount=Decimal("0.0177"),
        simulator_cost_amount=None,
        agent_turns=18,
    )


def test_summary_row_maps_status_counts() -> None:
    summary = _summary_from_row(EXPERIMENT_ROW)
    assert summary.experiment_id == "pilot-day5-20260913d"
    assert summary.status_counts == {
        "pending": 0,
        "running": 0,
        "succeeded": 18,
        "failed": 1,
        "infrastructure_error": 0,
        "interrupted": 1,
    }
    assert summary.reward_total == 0
    assert summary.agent_cost_total == Decimal("0.211203192")


def test_eval_endpoints_serve_records() -> None:
    summary = _summary_from_row(EXPERIMENT_ROW)
    app = create_app(
        event_source=object(),
        eval_source=FakeEvalSource(experiments=[summary]),
        run_directory=None,
    )
    client = TestClient(app)
    response = client.get("/api/eval/experiments")
    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["experiment_id"] == "pilot-day5-20260913d"
    assert payload[0]["status_counts"]["succeeded"] == 18

    attempts_response = client.get("/api/eval/experiments/pilot-day5-20260913d/attempts")
    assert attempts_response.status_code == 200
    assert attempts_response.json() == []


def test_runs_endpoint_serves_summaries() -> None:
    run_id = uuid4()
    app = create_app(
        event_source=object(),
        eval_source=None,
        run_directory=FakeRunDirectory(
            runs=[
                RunSummary(
                    run_id=run_id,
                    started_at=datetime(2026, 9, 14, 9, 0, tzinfo=UTC),
                    last_event_at=datetime(2026, 9, 14, 9, 15, tzinfo=UTC),
                    event_count=13,
                )
            ]
        ),
    )
    client = TestClient(app)
    response = client.get("/api/runs")
    assert response.status_code == 200
    payload = response.json()
    assert payload == [
        {
            "run_id": str(run_id),
            "started_at": "2026-09-14T09:00:00Z",
            "last_event_at": "2026-09-14T09:15:00Z",
            "event_count": 13,
        }
    ]


def test_app_without_optional_sources_keeps_sse_only_surface() -> None:
    app = create_app(event_source=object())
    client = TestClient(app)
    assert client.get("/api/eval/experiments").status_code == 404
    assert client.get("/api/runs").status_code == 404


def test_sse_router_still_present_with_optional_sources() -> None:
    app = create_app(
        event_source=object(),
        eval_source=FakeEvalSource(),
        run_directory=FakeRunDirectory(),
    )
    paths = set(app.openapi()["paths"])
    assert "/api/runs/{run_id}/events" in paths
    assert "/api/eval/experiments" in paths
    assert "/api/eval/experiments/{experiment_id}/attempts" in paths
    assert "/api/runs" in paths
