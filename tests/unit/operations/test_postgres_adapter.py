import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
import pytest
from pydantic import SecretStr

from commerce_agent.operations._approval import (
    ApprovalService,
    FixedClock,
    FixedNonceSource,
    HmacApprovalKeyring,
    StoredApproval,
)
from commerce_agent.operations._backtest import (
    METRIC_DEFINITIONS,
    AlertBacktestQuery,
)
from commerce_agent.operations._canonical import canonical_command
from commerce_agent.operations._postgres import (
    PostgresAlertObservationStore,
    PostgresOperationStore,
    PostgresSellerIdentityStore,
)
from commerce_agent.operations._seller_refs import PrivateSellerTarget
from commerce_agent.operations._store import (
    DefaultReferenceValidator,
    ProposalDraft,
    StoredProposal,
    ValidatedReferences,
)
from commerce_agent.operations.commands import CreateInvestigationTask, OpenSellerRiskCase
from commerce_agent.operations.contracts import (
    ActorContext,
    ActorRole,
    AlertBacktestRef,
    AlertBacktestRequest,
    AlertBacktestSnapshot,
    AlertComparator,
    AlertMetric,
    AlertWindowResult,
    CalendarWindow,
    CommandPreview,
    DecisionRequest,
    EvidenceRef,
    ExecuteRequest,
    MetricRef,
    ProposalRef,
    ProposalSnapshot,
    ProposalStatus,
    ProposeRequest,
    SellerRef,
    SellerTargetRequest,
)
from commerce_agent.operations.errors import (
    OperationConflictError,
    OperationInfrastructureError,
    OperationOutcomeUnknown,
)
from commerce_agent.operations.workflow import OperationWorkflow

IDENTITIES = {
    "proposal": (
        "proposal_writer",
        "commerce_operation_proposal",
        "ops,retail,trusted_schema,pg_catalog",
    ),
    "approval": (
        "approval_writer",
        "commerce_operation_approval",
        "ops,trusted_schema,pg_catalog",
    ),
    "execute": (
        "operation_executor",
        "commerce_operation_execute",
        "trusted_schema,ops,pg_catalog",
    ),
}


class NeverConnect:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def __call__(self, *args: object, **kwargs: object) -> Any:
        self.calls.append((args, kwargs))
        raise AssertionError("connection must not be attempted")


class FakeCursor:
    def __init__(
        self,
        result: tuple[object, ...] | list[tuple[object, ...]] | None = None,
    ) -> None:
        self._result = result

    async def fetchone(self) -> tuple[object, ...] | None:
        return self._result if isinstance(self._result, tuple) else None

    async def fetchall(self) -> list[tuple[object, ...]]:
        return self._result if isinstance(self._result, list) else []


class FakeConnection:
    def __init__(
        self,
        rows: dict[str, tuple[object, ...] | list[tuple[object, ...]]],
    ) -> None:
        self.rows = rows
        self.calls: list[tuple[str, object | None]] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    async def execute(self, query: str, params: object | None = None) -> FakeCursor:
        self.calls.append((query, params))
        result = next((value for marker, value in self.rows.items() if marker in query), None)
        return FakeCursor(result)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def close(self) -> None:
        self.closed = True


class FakeConnect:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def __call__(self, *args: object, **kwargs: object) -> FakeConnection:
        self.calls.append((args, kwargs))
        return self.connection


class RoutedConnect:
    def __init__(self, connections: dict[str, FakeConnection]) -> None:
        self.connections = connections
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def __call__(self, *args: object, **kwargs: object) -> FakeConnection:
        self.calls.append((args, kwargs))
        value = str(args[0])
        role = next(role for role in self.connections if f"//{role}:" in value)
        return self.connections[role]


class SerializationFailureConnection(FakeConnection):
    async def execute(self, query: str, params: object | None = None) -> FakeCursor:
        if "trusted_schema.create_operation_proposal" in query:
            raise psycopg.errors.SerializationFailure("private-server-detail")
        return await super().execute(query, params)


class CommitOutcomeUnknownConnection(FakeConnection):
    async def commit(self) -> None:
        raise psycopg.OperationalError("postgresql://private-server-detail")


class UniqueViolationConnection(FakeConnection):
    async def execute(self, query: str, params: object | None = None) -> FakeCursor:
        if "trusted_schema.create_operation_proposal" in query:
            raise psycopg.errors.UniqueViolation("private-constraint-detail")
        return await super().execute(query, params)


class ExecuteUniqueViolationConnection(FakeConnection):
    async def execute(self, query: str, params: object | None = None) -> FakeCursor:
        if "trusted_schema.execute_" in query:
            self.calls.append((query, params))
            raise psycopg.errors.UniqueViolation("private-constraint-detail")
        return await super().execute(query, params)


class SequentialConnect:
    def __init__(self, *connections: FakeConnection) -> None:
        self.connections = connections
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def __call__(self, *args: object, **kwargs: object) -> FakeConnection:
        self.calls.append((args, kwargs))
        return self.connections[len(self.calls) - 1]


def dsn(
    identity: str,
    *,
    role: str | None = None,
    application_name: str | None = None,
    database: str = "commerce_analyst",
    host: str = "127.0.0.1",
    port: str = "5432",
    search_path: str | None = None,
) -> SecretStr:
    expected_role, expected_application, expected_path = IDENTITIES[identity]
    return SecretStr(
        f"postgresql://{role or expected_role}:test-only@{host}:{port}/{database}"
        f"?application_name={application_name or expected_application}"
        f"&options=-csearch_path%3D{search_path or expected_path}"
    )


def operation_store(
    connect: Callable[..., Awaitable[Any]],
    *,
    proposal_dsn: SecretStr | None = None,
    approval_dsn: SecretStr | None = None,
    execution_dsn: SecretStr | None = None,
) -> PostgresOperationStore:
    return PostgresOperationStore(
        proposal_dsn=proposal_dsn or dsn("proposal"),
        approval_dsn=approval_dsn or dsn("approval"),
        execution_dsn=execution_dsn or dsn("execute"),
        scenario_id="seller-risk-postgres-v1",
        connect=connect,
    )


def proposal_draft(now: datetime) -> ProposalDraft:
    evidence = EvidenceRef(kind="query", ref="query:failure", digest="f" * 64)
    command = CreateInvestigationTask(
        type="create_investigation_task",
        title="Review a transaction failure",
        priority="high",
        public_summary="Review one evidence-bound transaction failure.",
        evidence_refs=(evidence,),
        expected_target_version=0,
    )
    preview = CommandPreview(
        command_type=command.type,
        title=command.title,
        public_summary=command.public_summary,
        target_versions={"investigation_task": 0},
        affected_rows=1,
    )
    return ProposalDraft(
        request=ProposeRequest(
            actor=ActorContext(
                actor_id=UUID(int=41),
                role=ActorRole.ANALYST,
                authentication_ref="session:failure",
                authenticated_at=now,
            ),
            command=command,
            evidence_refs=(evidence,),
            idempotency_key="failure-proposal-001",
        ),
        preview=preview,
        payload_sha256=canonical_command(command, preview.target_versions).sha256,
        created_at=now,
        expires_at=now + timedelta(hours=24),
        validated_references=ValidatedReferences(evidence_refs=(evidence,)),
    )


@pytest.mark.parametrize(
    ("identity", "field", "wrong_value"),
    [
        ("proposal", "role", "approval_writer"),
        ("proposal", "application_name", "commerce_operation_approval"),
        ("proposal", "database", "other_database"),
        ("proposal", "host", "localhost"),
        ("proposal", "port", "5433"),
        ("proposal", "search_path", "trusted_schema,ops,pg_catalog"),
        ("approval", "role", "proposal_writer"),
        ("execute", "role", "trace_writer"),
    ],
)
@pytest.mark.asyncio
async def test_operation_store_rejects_wrong_database_identity_before_connect(
    identity: str,
    field: str,
    wrong_value: str,
) -> None:
    connect = NeverConnect()
    overrides = {field: wrong_value}
    bad_dsn = dsn(identity, **overrides)
    kwargs = {f"{identity if identity != 'execute' else 'execution'}_dsn": bad_dsn}
    store = operation_store(connect, **kwargs)

    with pytest.raises(OperationInfrastructureError) as caught:
        await store.open()

    assert caught.value.reason_code == "operation_database_identity_invalid"
    assert caught.value.retryable is False
    assert connect.calls == []


@pytest.mark.parametrize(
    "adapter_type",
    [PostgresSellerIdentityStore, PostgresAlertObservationStore],
)
@pytest.mark.asyncio
async def test_proposal_read_adapter_rejects_wrong_role_before_connect(
    adapter_type: type[PostgresSellerIdentityStore] | type[PostgresAlertObservationStore],
) -> None:
    connect = NeverConnect()
    adapter = adapter_type(dsn("proposal", role="approval_writer"), connect=connect)

    with pytest.raises(OperationInfrastructureError) as caught:
        await adapter.open()

    assert caught.value.reason_code == "operation_database_identity_invalid"
    assert connect.calls == []


@pytest.mark.asyncio
async def test_create_proposal_uses_fixed_function_and_bounded_transaction() -> None:
    now = datetime(2026, 9, 7, 4, 0, tzinfo=UTC)
    proposal_id = UUID(int=101)
    connection = FakeConnection(
        {"trusted_schema.create_operation_proposal": (proposal_id, 1, "pending")}
    )
    connect = FakeConnect(connection)
    store = operation_store(connect)
    evidence = EvidenceRef(kind="query", ref="query:risk", digest="a" * 64)
    command = CreateInvestigationTask(
        type="create_investigation_task",
        title="Review a fulfillment exception",
        priority="high",
        public_summary="Review the evidence-bound fulfillment exception.",
        evidence_refs=(evidence,),
        expected_target_version=0,
    )
    request = ProposeRequest(
        actor=ActorContext(
            actor_id=UUID(int=1),
            role=ActorRole.ANALYST,
            authentication_ref="session:analyst",
            authenticated_at=now,
        ),
        command=command,
        evidence_refs=(evidence,),
        idempotency_key="proposal-contract-001",
    )
    preview = CommandPreview(
        command_type=command.type,
        title=command.title,
        public_summary=command.public_summary,
        target_versions={"investigation_task": 0},
        affected_rows=1,
    )
    draft = ProposalDraft(
        request=request,
        preview=preview,
        payload_sha256=canonical_command(command, preview.target_versions).sha256,
        created_at=now,
        expires_at=now + timedelta(hours=24),
        validated_references=ValidatedReferences(evidence_refs=(evidence,)),
    )

    snapshot = await store.create_or_get_proposal(draft)

    assert snapshot.proposal_ref.proposal_id == proposal_id
    assert snapshot.status is ProposalStatus.PENDING
    assert connect.calls[0][1] == {"connect_timeout": 3}
    statements = [query for query, _params in connection.calls]
    assert statements[:4] == [
        "BEGIN",
        "SET LOCAL statement_timeout = '5s'",
        "SET LOCAL lock_timeout = '1s'",
        "SET LOCAL idle_in_transaction_session_timeout = '10s'",
    ]
    function_query, function_params = connection.calls[4]
    assert function_query.startswith("SELECT * FROM trusted_schema.create_operation_proposal(")
    assert function_query.count("%s") == 16
    assert function_params is not None
    assert "Review a fulfillment exception" not in function_query
    assert connection.committed is True
    assert connection.closed is True


@pytest.mark.asyncio
async def test_read_proposal_uses_fixed_function_and_reconstructs_typed_state() -> None:
    now = datetime(2026, 9, 7, 4, 30, tzinfo=UTC)
    draft = proposal_draft(now)
    proposal_id = UUID(int=151)
    canonical = canonical_command(draft.request.command, draft.preview.target_versions)
    connection = FakeConnection(
        {
            "trusted_schema.read_operation_proposal": (
                proposal_id,
                1,
                draft.request.actor.actor_id,
                draft.request.command.type,
                canonical.schema_version,
                json.loads(canonical.canonical_bytes),
                canonical.sha256,
                [item.model_dump(mode="json") for item in draft.request.evidence_refs],
                draft.preview.model_dump(mode="json"),
                draft.preview.target_versions,
                "pending",
                draft.expires_at,
                draft.request.idempotency_key,
                None,
                None,
                draft.created_at,
            )
        }
    )
    store = operation_store(FakeConnect(connection))

    recovered = await store.read_proposal(ProposalRef(proposal_id=proposal_id, version=1))

    assert recovered is not None
    assert recovered.requester_id == draft.request.actor.actor_id
    assert recovered.command == draft.request.command
    assert recovered.evidence_refs == draft.request.evidence_refs
    assert recovered.snapshot.preview == draft.preview
    query, params = connection.calls[4]
    assert query.startswith("SELECT * FROM trusted_schema.read_operation_proposal(")
    assert query.count("%s") == 3
    assert params == (proposal_id, 1, "seller-risk-postgres-v1")


@pytest.mark.asyncio
async def test_seller_proposal_persists_and_recovers_only_fixed_private_binding() -> None:
    now = datetime(2026, 9, 7, 4, 40, tzinfo=UTC)
    evidence = EvidenceRef(kind="query", ref="query:seller", digest="e" * 64)
    command = OpenSellerRiskCase(
        type="open_seller_risk_case",
        seller_ref=SellerRef(
            namespace="product:seller-target",
            token="opaque-seller-reference",
        ),
        observation_started_at=now - timedelta(days=30),
        observation_ended_at=now,
        metric_ref=MetricRef(name="late_delivery_rate", revision="metric.v1"),
        numerator=3,
        denominator=10,
        observed=Decimal("0.3000"),
        threshold=Decimal("0.2000"),
        title="Review seller delivery risk",
        priority="high",
        evidence_refs=(evidence,),
        expected_target_version=0,
    )
    preview = CommandPreview(
        command_type=command.type,
        title=command.title,
        public_summary="Create a seller-risk annotation and linked investigation task.",
        target_versions={"investigation_task": 0, "risk_annotation": 0},
        affected_rows=2,
    )
    binding = PrivateSellerTarget(
        seller_id="private-seller-001",
        evidence_digest=evidence.digest,
    )
    draft = ProposalDraft(
        request=ProposeRequest(
            actor=ActorContext(
                actor_id=UUID(int=51),
                role=ActorRole.ANALYST,
                authentication_ref="session:seller",
                authenticated_at=now,
            ),
            command=command,
            evidence_refs=(evidence,),
            idempotency_key="seller-proposal-001",
        ),
        preview=preview,
        payload_sha256=canonical_command(command, preview.target_versions).sha256,
        created_at=now,
        expires_at=now + timedelta(hours=24),
        validated_references=ValidatedReferences(
            evidence_refs=(evidence,), private_bindings=(binding,)
        ),
    )
    proposal_id = UUID(int=153)
    create_connection = FakeConnection(
        {"trusted_schema.create_operation_proposal": (proposal_id, 1, "pending")}
    )

    await operation_store(FakeConnect(create_connection)).create_or_get_proposal(draft)

    _query, params = create_connection.calls[4]
    assert isinstance(params, tuple)
    assert params[13:15] == (binding.seller_id, binding.evidence_digest)

    canonical = canonical_command(command, preview.target_versions)
    read_connection = FakeConnection(
        {
            "trusted_schema.read_operation_proposal": (
                proposal_id,
                1,
                draft.request.actor.actor_id,
                command.type,
                canonical.schema_version,
                json.loads(canonical.canonical_bytes),
                canonical.sha256,
                [evidence.model_dump(mode="json")],
                preview.model_dump(mode="json"),
                preview.target_versions,
                "pending",
                draft.expires_at,
                draft.request.idempotency_key,
                binding.seller_id,
                binding.evidence_digest,
                draft.created_at,
            )
        }
    )

    recovered = await operation_store(FakeConnect(read_connection)).read_proposal(
        ProposalRef(proposal_id=proposal_id, version=1)
    )

    assert recovered is not None
    assert recovered.validated_references.private_bindings == (binding,)


@pytest.mark.asyncio
async def test_read_decision_uses_fixed_function_and_reconstructs_approval() -> None:
    now = datetime(2026, 9, 7, 4, 45, tzinfo=UTC)
    proposal_id = UUID(int=152)
    requester_id = UUID(int=41)
    approver = ActorContext(
        actor_id=UUID(int=42),
        role=ActorRole.APPROVER,
        authentication_ref="session:recovery",
        authenticated_at=now,
    )
    approvals = ApprovalService(
        clock=FixedClock(now),
        nonce_source=FixedNonceSource(bytes(range(32))),
        keyring=HmacApprovalKeyring({1: b"task-15-unit-key"}, active_version=1),
    )
    proposal_ref = ProposalRef(proposal_id=proposal_id, version=1)
    grant, nonce_digest = approvals.issue_grant(
        proposal_ref=proposal_ref,
        payload_sha256="b" * 64,
        requester_id=requester_id,
        approver=approver,
        target_versions={"investigation_task": 0},
    )
    connection = FakeConnection(
        {
            "trusted_schema.read_approval_decision": (
                proposal_id,
                1,
                "approved",
                approver.actor_id,
                None,
                now,
                grant.payload_sha256,
                requester_id,
                grant.target_versions,
                nonce_digest,
                grant.key_version,
                grant.expires_at,
                "d" * 64,
            )
        }
    )
    store = operation_store(FakeConnect(connection))

    recovered = await store.read_decision(proposal_ref)

    assert recovered is not None
    assert recovered.status is ProposalStatus.APPROVED
    assert recovered.approver_id == approver.actor_id
    assert recovered.approval is not None
    assert recovered.approval.nonce_digest == nonce_digest
    assert recovered.grant_sha256 == "d" * 64
    query, params = connection.calls[4]
    assert query.startswith("SELECT * FROM trusted_schema.read_approval_decision(")
    assert query.count("%s") == 3
    assert params == (proposal_id, 1, "seller-risk-postgres-v1")


@pytest.mark.asyncio
async def test_operation_workflow_routes_each_phase_to_its_fixed_database_identity() -> None:
    now = datetime(2026, 9, 7, 5, 0, tzinfo=UTC)
    proposal_id = UUID(int=201)
    execution_id = uuid5(NAMESPACE_URL, f"operation:{proposal_id}:1")
    proposal_connection = FakeConnection(
        {"trusted_schema.create_operation_proposal": (proposal_id, 1, "pending")}
    )
    approval_connection = FakeConnection(
        {"trusted_schema.record_approval_decision": (proposal_id, 1, "approved", now)}
    )
    execution_connection = FakeConnection(
        {
            "trusted_schema.execute_create_investigation_task": (
                execution_id,
                0,
                1,
                "operation_succeeded",
                now,
                "Created one investigation task.",
                f"audit:{execution_id}",
            )
        }
    )
    connect = RoutedConnect(
        {
            "proposal_writer": proposal_connection,
            "approval_writer": approval_connection,
            "operation_executor": execution_connection,
        }
    )
    store = operation_store(connect)
    workflow = OperationWorkflow(
        store=store,
        references=DefaultReferenceValidator(),
        clock=FixedClock(now),
        approvals=ApprovalService(
            clock=FixedClock(now),
            nonce_source=FixedNonceSource(bytes(range(32))),
            keyring=HmacApprovalKeyring({1: b"task-15-unit-key"}, active_version=1),
        ),
    )
    evidence = EvidenceRef(kind="query", ref="query:workflow", digest="c" * 64)
    command = CreateInvestigationTask(
        type="create_investigation_task",
        title="Review an exception",
        priority="medium",
        public_summary="Review one evidence-bound exception.",
        evidence_refs=(evidence,),
        expected_target_version=0,
    )
    analyst = ActorContext(
        actor_id=UUID(int=11),
        role=ActorRole.ANALYST,
        authentication_ref="session:analyst",
        authenticated_at=now,
    )
    approver = ActorContext(
        actor_id=UUID(int=12),
        role=ActorRole.APPROVER,
        authentication_ref="session:approver",
        authenticated_at=now,
    )

    proposal = await workflow.propose(
        ProposeRequest(
            actor=analyst,
            command=command,
            evidence_refs=(evidence,),
            idempotency_key="workflow-proposal-001",
        )
    )
    proposal_canonical = canonical_command(command, proposal.preview.target_versions)
    proposal_connection.rows["trusted_schema.read_operation_proposal"] = (
        proposal_id,
        1,
        analyst.actor_id,
        command.type,
        proposal_canonical.schema_version,
        json.loads(proposal_canonical.canonical_bytes),
        proposal_canonical.sha256,
        [evidence.model_dump(mode="json")],
        proposal.preview.model_dump(mode="json"),
        proposal.preview.target_versions,
        "pending",
        proposal.expires_at,
        "workflow-proposal-001",
        None,
        None,
        proposal.created_at,
    )
    decision = await workflow.decide(
        DecisionRequest(
            actor=approver,
            proposal_ref=proposal.proposal_ref,
            decision="approve",
        )
    )
    assert decision.grant is not None
    decision_write_params = next(
        params
        for query, params in approval_connection.calls
        if "trusted_schema.record_approval_decision" in query
    )
    assert isinstance(decision_write_params, tuple)
    approval_connection.rows["trusted_schema.read_approval_decision"] = (
        proposal_id,
        1,
        "approved",
        approver.actor_id,
        None,
        now,
        proposal.payload_sha256,
        analyst.actor_id,
        proposal.preview.target_versions,
        decision_write_params[6],
        decision.grant.key_version,
        decision.grant.expires_at,
        decision_write_params[11],
    )
    receipt = await workflow.execute(ExecuteRequest(actor=approver, grant=decision.grant))

    assert receipt.execution_id == execution_id
    assert receipt.proposal_ref == proposal.proposal_ref
    assert receipt.before_version == 0
    assert receipt.after_version == 1
    approval_sql = [query for query, _params in approval_connection.calls]
    assert any("trusted_schema.record_approval_decision" in query for query in approval_sql)
    execution_sql = [query for query, _params in execution_connection.calls]
    typed_calls = [query for query in execution_sql if "trusted_schema.execute_" in query]
    assert len(typed_calls) == 1
    assert "trusted_schema.execute_create_investigation_task" in typed_calls[0]
    assert all(command.public_summary not in query for query in typed_calls)


@pytest.mark.asyncio
async def test_seller_identity_store_uses_fixed_candidate_function() -> None:
    started_at = datetime(2026, 8, 1, tzinfo=UTC)
    ended_at = datetime(2026, 9, 1, tzinfo=UTC)
    connection = FakeConnection(
        {
            "trusted_schema.find_seller_target_candidates": [
                ("private-seller-001", 3, 10, Decimal("0.300000"))
            ]
        }
    )
    store = PostgresSellerIdentityStore(dsn("proposal"), connect=FakeConnect(connection))
    request = SellerTargetRequest(
        metric_ref=MetricRef(
            name="late_delivery_rate", revision="metric.late_delivery_rate.v1"
        ),
        observation_started_at=started_at,
        observation_ended_at=ended_at,
        anomaly_threshold=Decimal("0.200000"),
        minimum_denominator=5,
        status_filters=("delivered",),
        evidence_digest="d" * 64,
    )

    observations = await store.find_candidates(request)

    assert observations[0].seller_id == "private-seller-001"
    assert observations[0].normalized_value == Decimal("0.300000")
    query, params = connection.calls[4]
    assert query.startswith("SELECT * FROM trusted_schema.find_seller_target_candidates(")
    assert query.count("%s") == 6
    assert params == (
        "late_delivery_rate",
        started_at,
        ended_at,
        Decimal("0.200000"),
        5,
        ["delivered"],
    )


@pytest.mark.asyncio
async def test_seller_identity_enumeration_uses_fixed_reviewed_template() -> None:
    connection = FakeConnection(
        {
            "trusted_schema.find_seller_target_candidates": [
                ("private-seller-001", 0, 10, Decimal(0)),
                ("private-seller-002", 1, 10, Decimal("0.1")),
            ]
        }
    )
    store = PostgresSellerIdentityStore(dsn("proposal"), connect=FakeConnect(connection))

    identities = await store.iter_allowed_identities()

    assert identities == ("private-seller-001", "private-seller-002")
    query, params = connection.calls[4]
    assert "trusted_schema.find_seller_target_candidates" in query
    assert params[0] == "cancellation_rate"
    assert params[3:5] == (Decimal(0), 1)
    assert params[5] == [
        "approved",
        "created",
        "processing",
        "invoiced",
        "shipped",
        "delivered",
        "canceled",
        "unavailable",
    ]


@pytest.mark.asyncio
async def test_alert_observation_store_uses_metric_specific_function() -> None:
    started_at = datetime(2026, 7, 1, tzinfo=UTC)
    ended_at = datetime(2026, 9, 1, tzinfo=UTC)
    first_end = datetime(2026, 8, 1, tzinfo=UTC)
    connection = FakeConnection(
        {
            "trusted_schema.read_late_delivery_backtest": [
                (started_at, first_end, 2, 10, Decimal("0.2"), Decimal(1))
            ]
        }
    )
    store = PostgresAlertObservationStore(dsn("proposal"), connect=FakeConnect(connection))
    request = AlertBacktestRequest(
        metric=AlertMetric.LATE_DELIVERY_RATE,
        metric_revision="metric.late_delivery_rate.v1",
        grain="global",
        window=CalendarWindow.MONTH,
        comparator=AlertComparator.GREATER_THAN_OR_EQUAL,
        threshold=Decimal("0.200000"),
        minimum_denominator=5,
        started_at=started_at,
        ended_at=ended_at,
        filter_refs=(),
    )

    observations = await store.observe(
        AlertBacktestQuery(
            definition=METRIC_DEFINITIONS[AlertMetric.LATE_DELIVERY_RATE],
            request=request,
        )
    )

    assert observations[0].window_started_at == started_at
    assert observations[0].numerator == 2
    assert observations[0].denominator == 10
    assert observations[0].complete is True
    query, params = connection.calls[4]
    assert "trusted_schema.read_late_delivery_backtest" in query
    assert params == (started_at, ended_at, "month", 5, ["delivered"])


@pytest.mark.asyncio
async def test_operation_store_persists_backtest_with_fixed_function() -> None:
    started_at = datetime(2026, 7, 1, tzinfo=UTC)
    ended_at = datetime(2026, 8, 1, tzinfo=UTC)
    backtest_id = UUID(int=301)
    spec_hash = "e" * 64
    connection = FakeConnection(
        {"trusted_schema.store_alert_backtest": (backtest_id, spec_hash)}
    )
    store = operation_store(FakeConnect(connection))
    request = AlertBacktestRequest(
        metric=AlertMetric.LATE_DELIVERY_RATE,
        metric_revision="metric.late_delivery_rate.v1",
        grain="global",
        window=CalendarWindow.MONTH,
        comparator=AlertComparator.GREATER_THAN,
        threshold=Decimal("0.200000"),
        minimum_denominator=5,
        started_at=started_at,
        ended_at=ended_at,
        filter_refs=(),
    )
    snapshot = AlertBacktestSnapshot(
        backtest_ref=AlertBacktestRef(
            backtest_id=backtest_id, rule_spec_sha256=spec_hash
        ),
        request=request,
        windows=(
            AlertWindowResult(
                window_started_at=started_at,
                window_ended_at=ended_at,
                numerator=3,
                denominator=10,
                normalized_value=Decimal("0.300000"),
                complete=True,
                hit=True,
            ),
        ),
        completed_at=ended_at,
    )

    stored = await store.store_alert_backtest(snapshot)

    assert stored == snapshot.backtest_ref
    query, params = connection.calls[4]
    assert query.startswith("SELECT * FROM trusted_schema.store_alert_backtest(")
    assert query.count("%s") == 7
    assert params[0:2] == (backtest_id, spec_hash)
    assert params[4] == Decimal("1.000000")
    assert params[5:] == ("seller-risk-postgres-v1", ended_at)


@pytest.mark.asyncio
async def test_serialization_failure_is_retryable_known_non_commit() -> None:
    connection = SerializationFailureConnection({})
    store = operation_store(FakeConnect(connection))

    with pytest.raises(OperationInfrastructureError) as caught:
        await store.create_or_get_proposal(
            proposal_draft(datetime(2026, 9, 7, 6, 0, tzinfo=UTC))
        )

    assert caught.value.reason_code == "operation_serialization_failure"
    assert caught.value.retryable is True
    assert connection.rolled_back is True
    assert connection.closed is True
    assert "private-server-detail" not in str(caught.value)
    assert "private-server-detail" not in repr(caught.value)


@pytest.mark.asyncio
async def test_commit_response_loss_is_outcome_unknown_and_sanitized() -> None:
    proposal_id = UUID(int=401)
    connection = CommitOutcomeUnknownConnection(
        {"trusted_schema.create_operation_proposal": (proposal_id, 1, "pending")}
    )
    store = operation_store(FakeConnect(connection))

    with pytest.raises(OperationOutcomeUnknown) as caught:
        await store.create_or_get_proposal(
            proposal_draft(datetime(2026, 9, 7, 6, 30, tzinfo=UTC))
        )

    assert caught.value.reason_code == "operation_commit_outcome_unknown"
    assert caught.value.retryable is True
    assert connection.rolled_back is False
    assert connection.closed is True
    assert "private-server-detail" not in str(caught.value)
    assert "private-server-detail" not in repr(caught.value)


@pytest.mark.asyncio
async def test_unique_violation_maps_to_sanitized_non_retryable_conflict() -> None:
    connection = UniqueViolationConnection({})
    store = operation_store(FakeConnect(connection))

    with pytest.raises(OperationConflictError) as caught:
        await store.create_or_get_proposal(
            proposal_draft(datetime(2026, 9, 7, 6, 45, tzinfo=UTC))
        )

    assert caught.value.reason_code == "operation_identity_conflict"
    assert caught.value.retryable is False
    assert connection.rolled_back is True
    assert "private-constraint-detail" not in repr(caught.value)


@pytest.mark.asyncio
async def test_execute_once_recovers_receipt_after_concurrent_identity_conflict() -> None:
    now = datetime(2026, 9, 7, 7, 0, tzinfo=UTC)
    draft = proposal_draft(now)
    proposal_ref = ProposalRef(proposal_id=UUID(int=402), version=1)
    approver = ActorContext(
        actor_id=UUID(int=42),
        role=ActorRole.APPROVER,
        authentication_ref="session:concurrent-execution",
        authenticated_at=now,
    )
    approvals = ApprovalService(
        clock=FixedClock(now),
        nonce_source=FixedNonceSource(bytes(range(32))),
        keyring=HmacApprovalKeyring({1: b"task-15-unit-key"}, active_version=1),
    )
    grant, nonce_digest = approvals.issue_grant(
        proposal_ref=proposal_ref,
        payload_sha256=draft.payload_sha256,
        requester_id=draft.request.actor.actor_id,
        approver=approver,
        target_versions=draft.preview.target_versions,
    )
    proposal = StoredProposal(
        snapshot=ProposalSnapshot(
            proposal_ref=proposal_ref,
            status=ProposalStatus.APPROVED,
            requester_id=draft.request.actor.actor_id,
            command_type=draft.request.command.type,
            payload_sha256=draft.payload_sha256,
            preview=draft.preview,
            created_at=draft.created_at,
            expires_at=draft.expires_at,
        ),
        command=draft.request.command,
        requester_id=draft.request.actor.actor_id,
        evidence_refs=draft.request.evidence_refs,
        idempotency_key=draft.request.idempotency_key,
        revises=None,
        validated_references=draft.validated_references,
    )
    approval = StoredApproval(
        proposal_ref=proposal_ref,
        payload_sha256=draft.payload_sha256,
        requester_id=draft.request.actor.actor_id,
        approver_id=approver.actor_id,
        target_versions=grant.target_versions,
        expires_at=grant.expires_at,
        nonce_digest=nonce_digest,
        key_version=grant.key_version,
    )
    execution_id = uuid5(
        NAMESPACE_URL,
        f"operation:{proposal_ref.proposal_id}:{proposal_ref.version}",
    )
    conflict_connection = ExecuteUniqueViolationConnection({})
    recovery_connection = FakeConnection(
        {
            "FROM ops.command_execution": (
                execution_id,
                proposal_ref.proposal_id,
                proposal_ref.version,
                draft.request.command.type,
                0,
                1,
                now,
                draft.preview.public_summary,
                f"audit:{execution_id}",
            )
        }
    )
    connect = SequentialConnect(conflict_connection, recovery_connection)
    store = operation_store(connect)

    receipt = await store.execute_once(proposal, approval, grant, now=now)

    assert receipt.execution_id == execution_id
    assert receipt.proposal_ref == proposal_ref
    assert receipt.before_version == 0
    assert receipt.after_version == 1
    assert len(connect.calls) == 2
    assert conflict_connection.rolled_back is True
    assert conflict_connection.closed is True
    assert recovery_connection.committed is True
    assert recovery_connection.closed is True
