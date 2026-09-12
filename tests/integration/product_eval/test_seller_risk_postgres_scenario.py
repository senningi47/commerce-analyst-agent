from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import psycopg
import pytest
from pydantic import SecretStr

from commerce_agent.operations._approval import (
    ApprovalService,
    HmacApprovalKeyring,
    SecretsNonceSource,
    SystemClock,
)
from commerce_agent.operations._postgres import (
    PostgresOperationStore,
    PostgresSellerIdentityStore,
)
from commerce_agent.operations._seller_refs import SellerRefKeyring, SellerTargetResolver
from commerce_agent.operations._store import DefaultReferenceValidator
from commerce_agent.operations.commands import OpenSellerRiskCase
from commerce_agent.operations.contracts import (
    ActorContext,
    DecisionRequest,
    EvidenceRef,
    ExecuteRequest,
    ExecutionReceipt,
    MetricRef,
    ProposeRequest,
    SellerTargetRequest,
)
from commerce_agent.operations.errors import OperationOutcomeUnknown
from commerce_agent.operations.workflow import OperationWorkflow
from commerce_agent.product_eval._reset import PostgresScenarioReset
from commerce_agent.query_engine._ast_policy import AstPolicy
from commerce_agent.query_engine._postgres import PostgresExecutor
from commerce_agent.query_engine.contracts import QueryRequest, QueryResult
from commerce_agent.query_engine.engine import QueryEngine
from commerce_agent.trace._postgres import PostgresTraceStore
from tests.integration.trace.test_product_trace import _event

pytestmark = pytest.mark.postgres

SCENARIO_ID = "seller-risk-postgres-v1"
SENTINEL_SCENARIO_ID = "seller-risk-postgres-sentinel-v1"
RESET_MANIFEST_REVISION = "scenario-reset-v1"
OBSERVATION_STARTED_AT = datetime(2016, 1, 1, tzinfo=UTC)
OBSERVATION_ENDED_AT = datetime(2019, 1, 1, tzinfo=UTC)
RISK_THRESHOLD = Decimal("0.200000")
MINIMUM_DENOMINATOR = 5

ConnectionFactory = Callable[..., Awaitable[Any]]


def _operation_store(
    scenario_id: str,
    *,
    connect: ConnectionFactory = psycopg.AsyncConnection.connect,
) -> PostgresOperationStore:
    return PostgresOperationStore(
        proposal_dsn=SecretStr(os.environ["PRODUCT_PROPOSAL_DATABASE_DSN"]),
        approval_dsn=SecretStr(os.environ["PRODUCT_APPROVAL_DATABASE_DSN"]),
        execution_dsn=SecretStr(os.environ["PRODUCT_OPERATION_DATABASE_DSN"]),
        scenario_id=scenario_id,
        connect=connect,
    )


def _workflow(
    store: PostgresOperationStore,
    references: DefaultReferenceValidator,
) -> OperationWorkflow:
    clock = SystemClock()
    return OperationWorkflow(
        store=store,
        references=references,
        clock=clock,
        approvals=ApprovalService(
            clock=clock,
            nonce_source=SecretsNonceSource(),
            keyring=HmacApprovalKeyring(
                {1: os.environ["PRODUCT_OPERATION_APPROVAL_HMAC_KEY_V1"].encode("utf-8")},
                active_version=1,
            ),
        ),
    )


def _seller_resolver() -> SellerTargetResolver:
    return SellerTargetResolver(
        store=PostgresSellerIdentityStore(SecretStr(os.environ["PRODUCT_PROPOSAL_DATABASE_DSN"])),
        keyring=SellerRefKeyring(
            {1: os.environ["PRODUCT_SELLER_REF_HMAC_KEY_V1"].encode("utf-8")},
            active_version=1,
        ),
        clock=SystemClock(),
    )


def _reset() -> PostgresScenarioReset:
    return PostgresScenarioReset(SecretStr(os.environ["PRODUCT_SCENARIO_RESET_DATABASE_DSN"]))


def _query_engine() -> QueryEngine:
    return QueryEngine(
        AstPolicy(),
        PostgresExecutor(SecretStr(os.environ["PRODUCT_DATABASE_DSN"])),
    )


async def _committed_object_counts(scenario_id: str) -> tuple[int, int, int, int]:
    connection = await psycopg.AsyncConnection.connect(os.environ["PRODUCT_POSTGRES_ADMIN_DSN"])
    try:
        cursor = await connection.execute(
            "SELECT "
            "(SELECT count(*) FROM ops.investigation_task WHERE scenario_id = %s), "
            "(SELECT count(*) FROM ops.risk_annotation WHERE scenario_id = %s), "
            "(SELECT count(*) FROM ops.command_execution "
            " WHERE scenario_id = %s AND status = 'succeeded'), "
            "(SELECT count(*) FROM ops.audit_event WHERE scenario_id = %s)",
            (scenario_id, scenario_id, scenario_id, scenario_id),
        )
        row = await cursor.fetchone()
        assert row is not None
        return tuple(int(value) for value in row)
    finally:
        await connection.rollback()
        await connection.close()


async def _trace_count(scenario_id: str) -> int:
    connection = await psycopg.AsyncConnection.connect(os.environ["PRODUCT_POSTGRES_ADMIN_DSN"])
    try:
        cursor = await connection.execute(
            "SELECT count(*) FROM app.product_trace_event WHERE scenario_id = %s",
            (scenario_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        return int(row[0])
    finally:
        await connection.rollback()
        await connection.close()


def _sentinel_event():
    event = _event()
    run_scope = event.run_scope.model_copy(
        update={
            "run_id": uuid5(NAMESPACE_URL, f"run:{SENTINEL_SCENARIO_ID}"),
            "subject_id": SENTINEL_SCENARIO_ID,
        }
    )
    return event.model_copy(
        update={
            "run_scope": run_scope,
            "attempt_id": uuid5(NAMESPACE_URL, f"attempt:{SENTINEL_SCENARIO_ID}"),
        }
    )


class _CommitLossConnection:
    def __init__(
        self,
        connection: psycopg.AsyncConnection[object],
        owner: _LoseFirstExecutionCommit,
    ) -> None:
        self._connection = connection
        self._owner = owner
        self._executed_typed_command = False

    async def execute(self, query: str, params: object | None = None) -> Any:
        if "trusted_schema.execute_" in query:
            self._executed_typed_command = True
        return await self._connection.execute(query, params)

    async def commit(self) -> None:
        await self._connection.commit()
        if self._executed_typed_command and not self._owner.lost:
            self._owner.lost = True
            raise psycopg.OperationalError("commit response unavailable")

    async def rollback(self) -> None:
        await self._connection.rollback()

    async def close(self) -> None:
        await self._connection.close()


class _LoseFirstExecutionCommit:
    def __init__(self) -> None:
        self.lost = False

    async def __call__(self, dsn: str, **kwargs: object) -> Any:
        connection = await psycopg.AsyncConnection.connect(dsn, **kwargs)
        if "operation_executor" in dsn and not self.lost:
            return _CommitLossConnection(connection, self)
        return connection


async def _risk_command(
    resolver: SellerTargetResolver,
) -> tuple[OpenSellerRiskCase, EvidenceRef, str]:
    evidence = EvidenceRef(
        kind="query",
        ref="query:seller_drilldown",
        digest="a" * 64,
    )
    candidates = await resolver.find_candidates(
        SellerTargetRequest(
            metric_ref=MetricRef(
                name="late_delivery_rate",
                revision="metric.late_delivery_rate.v1",
            ),
            observation_started_at=OBSERVATION_STARTED_AT,
            observation_ended_at=OBSERVATION_ENDED_AT,
            anomaly_threshold=RISK_THRESHOLD,
            minimum_denominator=MINIMUM_DENOMINATOR,
            status_filters=("delivered",),
            evidence_digest=evidence.digest,
        )
    )
    if not candidates:
        pytest.fail("reviewed seller-risk candidate set is empty", pytrace=False)
    candidate = candidates[0]
    private_target = await resolver.resolve(
        candidate.seller_ref,
        expected_evidence_digest=evidence.digest,
    )
    return (
        OpenSellerRiskCase(
            type="open_seller_risk_case",
            seller_ref=candidate.seller_ref,
            observation_started_at=candidate.observation_started_at,
            observation_ended_at=candidate.observation_ended_at,
            metric_ref=MetricRef(
                name="late_delivery_rate",
                revision="metric.late_delivery_rate.v1",
            ),
            numerator=candidate.numerator,
            denominator=candidate.denominator,
            observed=candidate.normalized_value,
            threshold=RISK_THRESHOLD,
            title="Investigate seller delivery risk",
            priority="high",
            evidence_refs=(evidence,),
        ),
        evidence,
        private_target.seller_id,
    )


def _assert_public_payload_is_sanitized(
    private_seller_id: str,
    *,
    command: OpenSellerRiskCase,
    receipt: ExecutionReceipt,
    baseline: QueryResult,
    tasks: QueryResult,
    risks: QueryResult,
    sensitivity_counts: dict[str, int],
) -> None:
    public_payload = {
        "command": command.model_dump(mode="json"),
        "receipt": receipt.model_dump(mode="json"),
        "sensitivity": {
            "all_sellers": baseline.model_dump(mode="json"),
            "selected_risk_status": risks.model_dump(mode="json"),
            "counts": sensitivity_counts,
        },
        "tasks": tasks.model_dump(mode="json"),
    }
    serialized = json.dumps(public_payload, ensure_ascii=False, sort_keys=True)
    if private_seller_id in serialized:
        pytest.fail("public scenario output leaked a private seller identity", pytrace=False)
    assert '"seller_id"' not in serialized
    assert '"seller_digest"' not in serialized


@pytest.mark.asyncio
async def test_seller_risk_write_recovers_and_reads_back_once(
    analyst_actor: ActorContext,
    approver_actor: ActorContext,
) -> None:
    reset = _reset()
    sentinel_trace = PostgresTraceStore(
        SecretStr(os.environ["PRODUCT_TRACE_DATABASE_DSN"]),
        scenario_id=SENTINEL_SCENARIO_ID,
    )
    try:
        await reset.reset(SCENARIO_ID, RESET_MANIFEST_REVISION)
        await reset.reset(SENTINEL_SCENARIO_ID, RESET_MANIFEST_REVISION)
        await sentinel_trace.append(_sentinel_event())
        await reset.reset(SCENARIO_ID, RESET_MANIFEST_REVISION)
        assert await _trace_count(SENTINEL_SCENARIO_ID) == 1

        resolver = _seller_resolver()
        references = DefaultReferenceValidator(seller_resolver=resolver)
        command, evidence, private_seller_id = await _risk_command(resolver)

        proposal_workflow = _workflow(_operation_store(SCENARIO_ID), references)
        proposal = await proposal_workflow.propose(
            ProposeRequest(
                actor=analyst_actor,
                command=command,
                evidence_refs=(evidence,),
                idempotency_key="seller-risk-postgres-proposal-v1",
            )
        )
        approval_workflow = _workflow(_operation_store(SCENARIO_ID), references)
        decision = await approval_workflow.decide(
            DecisionRequest(
                actor=approver_actor,
                proposal_ref=proposal.proposal_ref,
                decision="approve",
                reason="Reviewed the evidence-bound seller risk.",
            )
        )
        assert decision.grant is not None
        request = ExecuteRequest(actor=approver_actor, grant=decision.grant)

        commit_loss = _LoseFirstExecutionCommit()
        lossy_store = _operation_store(SCENARIO_ID, connect=commit_loss)
        lossy_workflow = _workflow(lossy_store, references)
        with pytest.raises(OperationOutcomeUnknown) as caught:
            await lossy_workflow.execute(request)
        assert caught.value.reason_code == "operation_commit_outcome_unknown"
        assert commit_loss.lost is True

        recovered = await lossy_store.read_execution(proposal.proposal_ref)
        assert recovered is not None
        retry_workflow = _workflow(_operation_store(SCENARIO_ID), references)
        assert await retry_workflow.execute(request) == recovered
        assert await _committed_object_counts(SCENARIO_ID) == (1, 1, 1, 1)

        query_engine = _query_engine()
        baseline = await query_engine.execute(
            QueryRequest(sql="SELECT COUNT(*) AS seller_count FROM retail.sellers")
        )
        tasks = await query_engine.execute(
            QueryRequest(sql="SELECT task_ref, status FROM ops_read.investigation_tasks LIMIT 1000")
        )
        risks = await query_engine.execute(
            QueryRequest(sql="SELECT seller_ref, status FROM ops_read.risk_annotations LIMIT 1000")
        )

        expected_task_ref = str(
            uuid5(
                NAMESPACE_URL,
                f"task:{proposal.proposal_ref.proposal_id}:{proposal.proposal_ref.version}",
            )
        )
        assert tasks.columns == ["task_ref", "status"]
        assert tasks.rows == [{"task_ref": expected_task_ref, "status": "open"}]
        assert risks.columns == ["seller_ref", "status"]
        assert risks.rows == [
            {
                "seller_ref": command.seller_ref.token,
                "status": "under_investigation",
            }
        ]
        assert baseline.row_count == 1
        sensitivity_counts = {
            "all_sellers": int(baseline.rows[0]["seller_count"]),
            "selected_risk_status": risks.row_count,
        }
        assert sensitivity_counts["all_sellers"] > sensitivity_counts["selected_risk_status"]
        _assert_public_payload_is_sanitized(
            private_seller_id,
            command=command,
            receipt=recovered,
            baseline=baseline,
            tasks=tasks,
            risks=risks,
            sensitivity_counts=sensitivity_counts,
        )

        await reset.reset(SCENARIO_ID, RESET_MANIFEST_REVISION)
        assert await _committed_object_counts(SCENARIO_ID) == (0, 0, 0, 0)
        assert await _trace_count(SENTINEL_SCENARIO_ID) == 1
    finally:
        try:
            await reset.reset(SCENARIO_ID, RESET_MANIFEST_REVISION)
        finally:
            await reset.reset(SENTINEL_SCENARIO_ID, RESET_MANIFEST_REVISION)
