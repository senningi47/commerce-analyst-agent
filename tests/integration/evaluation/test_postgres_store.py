"""Live PostgreSQL contract tests for the evaluation store (0005 applied).

Gated behind `-m postgres` + COMMERCE_AGENT_RUN_POSTGRES_TESTS=1: a default
skip is not evidence (HANDOFF pitfall 2). Each test uses a unique experiment
id and the harness deletes its rows via the admin DSN in a finally block,
verifying the eval-row count returns to zero — the least-privilege writer
role itself never issues DELETE.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from commerce_agent.evaluation._postgres import PostgresEvaluationStore
from commerce_agent.evaluation.contracts import (
    AttemptRecord,
    AttemptTelemetry,
    EpisodeResult,
    EvalStateConflict,
    EvalTaskStatus,
)

FIXED_TIME = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)

pytestmark = pytest.mark.postgres


def make_record(experiment_id: str, task_id: str = "task-1", mode: str = "c") -> AttemptRecord:
    return AttemptRecord(
        attempt_id=uuid4(),
        run_id=uuid4(),
        experiment_id=experiment_id,
        task_id=task_id,
        mode=mode,
        attempt_seq=1,
        status=EvalTaskStatus.PENDING,
        started_at=FIXED_TIME,
    )


@pytest.fixture
def evaluation_store() -> PostgresEvaluationStore:
    import os

    return PostgresEvaluationStore(
        dsn=os.environ["PRODUCT_EVALUATION_DATABASE_DSN"]
    )


@pytest.fixture
def scoped_experiment(evaluation_store: PostgresEvaluationStore) -> str:
    import os

    experiment_id = f"live-eval-test-{uuid.uuid4().hex[:12]}"
    admin_dsn = os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]

    def count() -> int:
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            cursor = connection.execute(
                "SELECT count(*) FROM eval.task_attempt WHERE experiment_id = %s",
                (experiment_id,),
            )
            return int(cursor.fetchone()[0])

    # the real flow always registers the experiment (Runner.run) before any
    # attempt; the FK task_attempt_experiment_id_fkey enforces exactly this
    evaluation_store.register_experiment(
        experiment_id=experiment_id, purpose="pilot", config_hash="a" * 64
    )
    assert count() == 0
    try:
        yield experiment_id
    finally:
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
    assert count() == 0


def test_experiment_registration_is_idempotent_and_guarded(
    evaluation_store: PostgresEvaluationStore, scoped_experiment: str
) -> None:
    evaluation_store.register_experiment(
        experiment_id=scoped_experiment, purpose="pilot", config_hash="a" * 64
    )
    evaluation_store.register_experiment(
        experiment_id=scoped_experiment, purpose="pilot", config_hash="a" * 64
    )
    with pytest.raises(EvalStateConflict) as excinfo:
        evaluation_store.register_experiment(
            experiment_id=scoped_experiment, purpose="pilot", config_hash="b" * 64
        )
    assert excinfo.value.reason_code == "experiment_identity_conflict"


def test_duplicate_attempt_identity_conflicts(
    evaluation_store: PostgresEvaluationStore, scoped_experiment: str
) -> None:
    record = make_record(scoped_experiment)
    evaluation_store.register_attempt(record)
    with pytest.raises(EvalStateConflict) as excinfo:
        evaluation_store.register_attempt(record)
    assert excinfo.value.reason_code == "attempt_identity_conflict"


def test_full_lifecycle_with_telemetry_round_trip(
    evaluation_store: PostgresEvaluationStore, scoped_experiment: str
) -> None:
    record = make_record(scoped_experiment)
    evaluation_store.register_attempt(record)
    evaluation_store.mark_running(record.attempt_id, expected_status=EvalTaskStatus.PENDING)

    telemetry = AttemptTelemetry(
        rounds=3, tool_calls=5, wall_clock_ms=1500, cache_hit_ratio=0.42
    )
    evaluation_store.finish_attempt(
        record.attempt_id,
        status=EvalTaskStatus.INTERRUPTED,
        error_class=None,
        telemetry=telemetry,
    )

    unfinished = evaluation_store.unfinished_attempts(scoped_experiment)
    assert len(unfinished) == 1
    assert unfinished[0].attempt_id == record.attempt_id
    assert unfinished[0].status == EvalTaskStatus.INTERRUPTED
    assert unfinished[0].telemetry == telemetry


def test_double_finish_conflicts_live(
    evaluation_store: PostgresEvaluationStore, scoped_experiment: str
) -> None:
    record = make_record(scoped_experiment)
    evaluation_store.register_attempt(record)
    evaluation_store.mark_running(record.attempt_id, expected_status=EvalTaskStatus.PENDING)
    evaluation_store.finish_attempt(
        record.attempt_id,
        status=EvalTaskStatus.SUCCEEDED,
        error_class=None,
        telemetry=AttemptTelemetry(),
        result=EpisodeResult(reward=Decimal("0.5"), phase1_passed=True),
    )
    with pytest.raises(EvalStateConflict) as excinfo:
        evaluation_store.finish_attempt(
            record.attempt_id,
            status=EvalTaskStatus.FAILED,
            error_class="x",
            telemetry=AttemptTelemetry(),
        )
    assert excinfo.value.reason_code == "status_transition_conflict"


def test_failed_attempt_cannot_carry_result_live(
    evaluation_store: PostgresEvaluationStore, scoped_experiment: str
) -> None:
    record = make_record(scoped_experiment)
    evaluation_store.register_attempt(record)
    evaluation_store.mark_running(record.attempt_id, expected_status=EvalTaskStatus.PENDING)
    with pytest.raises(EvalStateConflict) as excinfo:
        evaluation_store.finish_attempt(
            record.attempt_id,
            status=EvalTaskStatus.FAILED,
            error_class="evaluator_rejected",
            telemetry=AttemptTelemetry(),
            result=EpisodeResult(reward=Decimal("1.0")),
        )
    assert excinfo.value.reason_code == "result_requires_succeeded_attempt"
    # rollback leaves the attempt running, not half-finished
    unfinished = evaluation_store.unfinished_attempts(scoped_experiment)
    assert [item.status for item in unfinished] == [EvalTaskStatus.RUNNING]


def test_completed_tasks_and_result_readback(
    evaluation_store: PostgresEvaluationStore, scoped_experiment: str
) -> None:
    record = make_record(scoped_experiment, task_id="task-9", mode="a")
    evaluation_store.register_attempt(record)
    evaluation_store.mark_running(record.attempt_id, expected_status=EvalTaskStatus.PENDING)
    result = EpisodeResult(
        reward=Decimal("0.75"),
        phase1_passed=True,
        phase2_passed=False,
        rounds=2,
        tool_calls=4,
        submit_count=1,
    )
    evaluation_store.finish_attempt(
        record.attempt_id,
        status=EvalTaskStatus.SUCCEEDED,
        error_class=None,
        telemetry=AttemptTelemetry(),
        result=result,
    )

    completed = evaluation_store.completed_tasks(scoped_experiment)
    assert completed == {("task-9", "a")}
    assert evaluation_store.completed_tasks(scoped_experiment) == completed  # idempotent
    assert evaluation_store.result(record.attempt_id) == result
