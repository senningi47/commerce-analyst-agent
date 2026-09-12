"""Role-bound PostgreSQL adapters for controlled Product operations."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Self
from uuid import NAMESPACE_URL, uuid4, uuid5

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb
from pydantic import SecretStr, TypeAdapter, ValidationError

from commerce_agent.operations._approval import StoredApproval, execution_grant_sha256
from commerce_agent.operations._backtest import AlertBacktestQuery, AlertObservation
from commerce_agent.operations._canonical import canonical_command, canonical_json_bytes
from commerce_agent.operations._seller_refs import (
    PrivateSellerObservation,
    PrivateSellerTarget,
)
from commerce_agent.operations._store import (
    ProposalDraft,
    StoredDecision,
    StoredProposal,
    ValidatedReferences,
)
from commerce_agent.operations.commands import (
    AddInvestigationConclusion,
    AssignInvestigation,
    CloseInvestigation,
    CreateAndEnableMetricAlertRule,
    CreateInvestigationFromAlertHit,
    CreateInvestigationTask,
    OpenSellerRiskCase,
    OperationCommand,
    TransitionInvestigation,
)
from commerce_agent.operations.contracts import (
    AlertBacktestRef,
    AlertBacktestSnapshot,
    AlertMetric,
    CommandPreview,
    DecisionReceipt,
    EvidenceRef,
    ExecutionGrant,
    ExecutionReceipt,
    ExecutionStatus,
    MetricRef,
    ProposalRef,
    ProposalSnapshot,
    ProposalStatus,
    SellerTargetRequest,
)
from commerce_agent.operations.errors import (
    ApprovalError,
    OperationAuthorizationError,
    OperationConflictError,
    OperationContractError,
    OperationInfrastructureError,
    OperationOutcomeUnknown,
)

ConnectionFactory = Callable[..., Awaitable[Any]]

_SCENARIO_ID = re.compile(r"^[a-z][a-z0-9-]{2,127}-v[0-9]+$")


@dataclass(frozen=True)
class _DatabaseIdentity:
    role: str
    application_name: str
    search_path: str


_PROPOSAL_IDENTITY = _DatabaseIdentity(
    role="proposal_writer",
    application_name="commerce_operation_proposal",
    search_path="ops,retail,trusted_schema,pg_catalog",
)
_APPROVAL_IDENTITY = _DatabaseIdentity(
    role="approval_writer",
    application_name="commerce_operation_approval",
    search_path="ops,trusted_schema,pg_catalog",
)
_EXECUTION_IDENTITY = _DatabaseIdentity(
    role="operation_executor",
    application_name="commerce_operation_execute",
    search_path="trusted_schema,ops,pg_catalog",
)

_TRANSACTION_STATEMENTS = (
    "SET LOCAL statement_timeout = '5s'",
    "SET LOCAL lock_timeout = '1s'",
    "SET LOCAL idle_in_transaction_session_timeout = '10s'",
)
_BACKTEST_FUNCTIONS = {
    AlertMetric.LATE_DELIVERY_RATE: "read_late_delivery_backtest",
    AlertMetric.LOW_RATING_RATE: "read_low_rating_backtest",
    AlertMetric.CANCELLATION_RATE: "read_cancellation_backtest",
}
_METRIC_STATUS_FILTERS = {
    AlertMetric.LATE_DELIVERY_RATE: ("delivered",),
    AlertMetric.LOW_RATING_RATE: ("delivered",),
    AlertMetric.CANCELLATION_RATE: (
        "approved",
        "created",
        "processing",
        "invoiced",
        "shipped",
        "delivered",
        "canceled",
        "unavailable",
    ),
}
_OPERATION_COMMAND_ADAPTER = TypeAdapter(OperationCommand)


def _validate_dsn(dsn: SecretStr, identity: _DatabaseIdentity) -> None:
    try:
        parameters = conninfo_to_dict(dsn.get_secret_value())
    except Exception as error:
        raise OperationInfrastructureError(
            "operation_database_identity_invalid", retryable=False
        ) from error
    required = {
        "user": identity.role,
        "host": "127.0.0.1",
        "port": "5432",
        "dbname": "commerce_analyst",
        "application_name": identity.application_name,
        "options": f"-csearch_path={identity.search_path}",
    }
    if any(parameters.get(key) != value for key, value in required.items()):
        raise OperationInfrastructureError(
            "operation_database_identity_invalid", retryable=False
        )


async def _rollback_quietly(connection: Any) -> None:
    try:
        await connection.rollback()
    except psycopg.Error:
        return


async def _close_quietly(connection: Any) -> None:
    try:
        await connection.close()
    except psycopg.Error:
        return


def _database_error(error: psycopg.Error) -> Exception:
    if isinstance(error, psycopg.errors.UniqueViolation):
        return OperationConflictError("operation_identity_conflict", retryable=False)
    if isinstance(error, psycopg.errors.IntegrityConstraintViolation):
        return OperationConflictError("operation_state_conflict", retryable=False)
    if isinstance(error, psycopg.errors.InsufficientPrivilege):
        return OperationAuthorizationError(
            "operation_database_authorization_rejected", retryable=False
        )
    if isinstance(error, psycopg.errors.NoDataFound):
        return ApprovalError("proposal_not_found", retryable=False)
    if isinstance(error, psycopg.errors.InvalidParameterValue):
        return OperationContractError("operation_parameter_rejected", retryable=False)
    return OperationInfrastructureError("operation_database_rejected", retryable=False)


async def _run_transaction(
    *,
    dsn: SecretStr,
    identity: _DatabaseIdentity,
    connect: ConnectionFactory,
    operation: Callable[[Any], Awaitable[Any]],
    begin_statement: str = "BEGIN",
) -> Any:
    _validate_dsn(dsn, identity)
    try:
        connection = await connect(dsn.get_secret_value(), connect_timeout=3)
    except psycopg.Error as error:
        raise OperationInfrastructureError(
            "operation_connection_failed", retryable=True
        ) from error

    try:
        try:
            await connection.execute(begin_statement)
            for statement in _TRANSACTION_STATEMENTS:
                await connection.execute(statement)
            result = await operation(connection)
        except psycopg.errors.SerializationFailure as error:
            await _rollback_quietly(connection)
            raise OperationInfrastructureError(
                "operation_serialization_failure", retryable=True
            ) from error
        except psycopg.Error as error:
            await _rollback_quietly(connection)
            raise _database_error(error) from error
        except Exception:
            await _rollback_quietly(connection)
            raise

        try:
            await connection.commit()
        except psycopg.Error as error:
            raise OperationOutcomeUnknown(
                "operation_commit_outcome_unknown", retryable=True
            ) from error
        return result
    finally:
        await _close_quietly(connection)


def _evidence_json(proposal: StoredProposal) -> Jsonb:
    return Jsonb([item.model_dump(mode="json") for item in proposal.evidence_refs])


def _seller_digest(token: str) -> str:
    try:
        padded = token + "=" * (-len(token) % 4)
        encoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        canonical, _signature = encoded.rsplit(b".", 1)
        payload = json.loads(canonical)
        digest = payload["seller_digest"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError
        return digest
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeError) as error:
        raise OperationContractError("seller_ref_invalid", retryable=False) from error


def _private_seller_binding(
    command: OperationCommand, validated: ValidatedReferences
) -> tuple[str | None, str | None]:
    targets = tuple(
        item for item in validated.private_bindings if isinstance(item, PrivateSellerTarget)
    )
    if len(targets) != len(validated.private_bindings):
        raise OperationContractError("operation_private_binding_invalid", retryable=False)
    if isinstance(command, OpenSellerRiskCase):
        query_evidence = tuple(
            item for item in validated.evidence_refs if item.kind == "query"
        )
        if (
            len(targets) != 1
            or len(query_evidence) != 1
            or targets[0].evidence_digest != query_evidence[0].digest
        ):
            raise OperationContractError("seller_private_binding_invalid", retryable=False)
        return targets[0].seller_id, targets[0].evidence_digest
    if targets:
        raise OperationContractError("operation_private_binding_invalid", retryable=False)
    return None, None


def _stored_proposal_from_row(
    row: tuple[object, ...], proposal_ref: ProposalRef
) -> StoredProposal:
    try:
        if len(row) != 16 or (row[0], row[1]) != (
            proposal_ref.proposal_id,
            proposal_ref.version,
        ):
            raise ValueError
        canonical_payload = row[5]
        target_versions = row[9]
        if not isinstance(canonical_payload, dict) or not isinstance(target_versions, dict):
            raise TypeError
        if set(canonical_payload) != {"command", "schema_version", "target_versions"}:
            raise ValueError
        if canonical_payload["schema_version"] != row[4]:
            raise ValueError
        command = _OPERATION_COMMAND_ADAPTER.validate_python(canonical_payload["command"])
        evidence_refs = tuple(EvidenceRef.model_validate(item) for item in row[7])
        query_evidence = tuple(item for item in evidence_refs if item.kind == "query")
        preview = CommandPreview.model_validate(row[8])
        canonical = canonical_command(command, target_versions, schema_version=row[4])
        if (
            command.type != row[3]
            or command.evidence_refs != evidence_refs
            or preview.command_type != command.type
            or preview.target_versions != target_versions
            or canonical_payload != json.loads(canonical.canonical_bytes)
            or canonical.sha256 != row[6]
        ):
            raise ValueError
        if row[13] is None and row[14] is None:
            private_bindings: tuple[object, ...] = ()
        elif (
            isinstance(command, OpenSellerRiskCase)
            and isinstance(row[13], str)
            and isinstance(row[14], str)
            and re.fullmatch(r"[0-9a-f]{64}", row[14])
            and len(query_evidence) == 1
            and row[14] == query_evidence[0].digest
        ):
            private_bindings = (
                PrivateSellerTarget(seller_id=row[13], evidence_digest=row[14]),
            )
        else:
            raise ValueError
        if isinstance(command, OpenSellerRiskCase) != bool(private_bindings):
            raise ValueError
        snapshot = ProposalSnapshot(
            proposal_ref=proposal_ref,
            status=ProposalStatus(row[10]),
            requester_id=row[2],
            command_type=row[3],
            payload_sha256=row[6],
            preview=preview,
            created_at=row[15],
            expires_at=row[11],
        )
        return StoredProposal(
            snapshot=snapshot,
            command=command,
            requester_id=row[2],
            evidence_refs=evidence_refs,
            idempotency_key=row[12],
            revises=(
                ProposalRef(proposal_id=proposal_ref.proposal_id, version=proposal_ref.version - 1)
                if proposal_ref.version > 1
                else None
            ),
            validated_references=ValidatedReferences(
                evidence_refs=evidence_refs,
                private_bindings=private_bindings,
            ),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        raise OperationInfrastructureError(
            "operation_result_shape_invalid", retryable=False
        ) from error


def _stored_decision_from_row(
    row: tuple[object, ...], proposal_ref: ProposalRef
) -> StoredDecision:
    try:
        if len(row) != 13 or (row[0], row[1]) != (
            proposal_ref.proposal_id,
            proposal_ref.version,
        ):
            raise ValueError
        status = ProposalStatus(row[2])
        if status is ProposalStatus.APPROVED:
            if (
                not all(row[index] is not None for index in (9, 10, 11, 12))
                or not isinstance(row[12], str)
                or re.fullmatch(r"[0-9a-f]{64}", row[12]) is None
            ):
                raise ValueError
            approval = StoredApproval(
                proposal_ref=proposal_ref,
                payload_sha256=row[6],
                requester_id=row[7],
                approver_id=row[3],
                target_versions=row[8],
                expires_at=row[11],
                nonce_digest=row[9],
                key_version=row[10],
            )
            grant_sha256 = row[12]
        elif status is ProposalStatus.REJECTED:
            if any(row[index] is not None for index in (9, 10, 11, 12)):
                raise ValueError
            approval = None
            grant_sha256 = None
        else:
            raise ValueError
        return StoredDecision(
            status=status,
            approver_id=row[3],
            decided_at=row[5],
            reason=row[4],
            approval=approval,
            grant_sha256=grant_sha256,
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise OperationInfrastructureError(
            "operation_result_shape_invalid", retryable=False
        ) from error


def _command_call(
    proposal: StoredProposal, ref: ProposalRef
) -> tuple[str, tuple[object, ...]]:
    command = proposal.command
    if isinstance(command, OpenSellerRiskCase):
        targets = tuple(
            item
            for item in proposal.validated_references.private_bindings
            if isinstance(item, PrivateSellerTarget)
        )
        query_evidence = tuple(item for item in proposal.evidence_refs if item.kind == "query")
        if (
            len(targets) != 1
            or len(query_evidence) != 1
            or targets[0].evidence_digest != query_evidence[0].digest
        ):
            raise OperationContractError("seller_private_binding_invalid", retryable=False)
        return (
            "execute_open_seller_risk_case",
            (
                uuid5(NAMESPACE_URL, f"task:{ref.proposal_id}:{ref.version}"),
                uuid5(NAMESPACE_URL, f"risk:{ref.proposal_id}:{ref.version}"),
                targets[0].seller_id,
                _seller_digest(command.seller_ref.token),
                command.seller_ref.token,
                command.observation_started_at,
                command.observation_ended_at,
                command.metric_ref.name,
                command.metric_ref.revision,
                command.numerator,
                command.denominator,
                command.observed,
                command.threshold,
                command.title,
                command.priority,
                _evidence_json(proposal),
            ),
        )
    if isinstance(command, CreateInvestigationTask):
        return (
            "execute_create_investigation_task",
            (
                uuid5(NAMESPACE_URL, f"task:{ref.proposal_id}:{ref.version}"),
                command.title,
                command.priority,
                command.public_summary,
                "Evidence-bound investigation request.",
            ),
        )
    if isinstance(command, CreateInvestigationFromAlertHit):
        return (
            "execute_create_investigation_from_alert_hit",
            (
                uuid5(NAMESPACE_URL, f"task:{ref.proposal_id}:{ref.version}"),
                command.alert_hit_ref.hit_id,
                command.title,
                command.priority,
                "Evidence-bound enabled alert hit.",
            ),
        )
    if isinstance(command, CreateAndEnableMetricAlertRule):
        return (
            "execute_create_and_enable_metric_alert_rule",
            (
                uuid5(NAMESPACE_URL, f"rule:{ref.proposal_id}:{ref.version}"),
                command.alert_backtest_ref.backtest_id,
                command.metric_ref.name,
                command.metric_ref.revision,
                command.grain,
                command.window,
                command.comparator,
                command.threshold,
                command.minimum_denominator,
                Jsonb(list(command.filter_refs)),
                command.alert_backtest_ref.rule_spec_sha256,
            ),
        )
    if isinstance(command, AssignInvestigation):
        return (
            "execute_assign_investigation",
            (command.task_ref.task_id, command.expected_target_version, command.assignee_ref),
        )
    if isinstance(command, TransitionInvestigation):
        return (
            "execute_transition_investigation",
            (
                command.task_ref.task_id,
                command.expected_target_version,
                command.from_status.value,
                command.to_status.value,
                command.reason_code,
            ),
        )
    if isinstance(command, AddInvestigationConclusion):
        return (
            "execute_add_investigation_conclusion",
            (
                command.task_ref.task_id,
                command.expected_target_version,
                f"conclusion:{ref.proposal_id}:{ref.version}",
                command.conclusion_code,
                command.conclusion_summary,
                _evidence_json(proposal),
            ),
        )
    if isinstance(command, CloseInvestigation):
        return (
            "execute_close_investigation",
            (
                command.task_ref.task_id,
                command.expected_target_version,
                command.conclusion_ref,
                command.risk_disposition.value if command.risk_disposition else None,
            ),
        )
    raise OperationContractError("operation_command_unsupported", retryable=False)


class PostgresOperationStore:
    """OperationStore split across proposal, approval, and executor roles."""

    def __init__(
        self,
        *,
        proposal_dsn: SecretStr,
        approval_dsn: SecretStr,
        execution_dsn: SecretStr,
        scenario_id: str,
        connect: ConnectionFactory = psycopg.AsyncConnection.connect,
    ) -> None:
        self._proposal_dsn = proposal_dsn
        self._approval_dsn = approval_dsn
        self._execution_dsn = execution_dsn
        self._scenario_id = scenario_id
        self._connect = connect

    async def open(self) -> Self:
        _validate_dsn(self._proposal_dsn, _PROPOSAL_IDENTITY)
        _validate_dsn(self._approval_dsn, _APPROVAL_IDENTITY)
        _validate_dsn(self._execution_dsn, _EXECUTION_IDENTITY)
        if not _SCENARIO_ID.fullmatch(self._scenario_id):
            raise OperationInfrastructureError(
                "operation_scenario_identity_invalid", retryable=False
            )
        return self

    async def preview_command(
        self, command: OperationCommand, validated: ValidatedReferences
    ) -> CommandPreview:
        del validated
        if isinstance(command, OpenSellerRiskCase):
            versions = {"investigation_task": 0, "risk_annotation": 0}
            affected_rows = 2
            summary = "Create a seller-risk annotation and linked investigation task."
        elif isinstance(command, (CreateInvestigationTask, CreateInvestigationFromAlertHit)):
            versions = {"investigation_task": 0}
            affected_rows = 1
            summary = "Create one investigation task."
        elif isinstance(command, CreateAndEnableMetricAlertRule):
            versions = {"metric_alert_rule": 0}
            affected_rows = 1
            summary = "Create one enabled metric alert rule."
        else:
            versions = {"investigation_task": command.expected_target_version}
            affected_rows = 1
            summary = "Update one investigation task at its exact version."
        return CommandPreview(
            command_type=command.type,
            title=getattr(command, "title", command.type.replace("_", " ").title()),
            public_summary=summary,
            target_versions=versions,
            affected_rows=affected_rows,
        )

    async def create_or_get_proposal(self, draft: ProposalDraft) -> ProposalSnapshot:
        await self.open()
        canonical = canonical_command(
            draft.request.command, draft.preview.target_versions
        )
        if canonical.sha256 != draft.payload_sha256:
            raise OperationContractError(
                "proposal_payload_hash_mismatch", retryable=False
            )
        if draft.request.revises is None:
            requested_ref = ProposalRef(proposal_id=uuid4(), version=1)
        else:
            requested_ref = ProposalRef(
                proposal_id=draft.request.revises.proposal_id,
                version=draft.request.revises.version + 1,
            )
        target_versions_sha256 = hashlib.sha256(
            canonical_json_bytes(draft.preview.target_versions)
        ).hexdigest()
        private_seller_id, private_evidence_digest = _private_seller_binding(
            draft.request.command, draft.validated_references
        )

        async def create(connection: Any) -> tuple[object, ...]:
            cursor = await connection.execute(
                "SELECT * FROM trusted_schema.create_operation_proposal("
                + ", ".join(["%s"] * 16)
                + ")",
                (
                    requested_ref.proposal_id,
                    requested_ref.version,
                    draft.request.actor.actor_id,
                    draft.request.command.type,
                    canonical.schema_version,
                    Jsonb(json.loads(canonical.canonical_bytes)),
                    canonical.sha256,
                    Jsonb(
                        [item.model_dump(mode="json") for item in draft.request.evidence_refs]
                    ),
                    Jsonb(draft.preview.model_dump(mode="json")),
                    Jsonb(draft.preview.target_versions),
                    target_versions_sha256,
                    draft.expires_at,
                    draft.request.idempotency_key,
                    private_seller_id,
                    private_evidence_digest,
                    self._scenario_id,
                ),
            )
            row = await cursor.fetchone()
            if row is None or len(row) != 3:
                raise OperationInfrastructureError(
                    "operation_result_shape_invalid", retryable=False
                )
            return row

        row = await _run_transaction(
            dsn=self._proposal_dsn,
            identity=_PROPOSAL_IDENTITY,
            connect=self._connect,
            operation=create,
        )
        proposal_ref = ProposalRef(proposal_id=row[0], version=row[1])
        snapshot = ProposalSnapshot(
            proposal_ref=proposal_ref,
            status=ProposalStatus(row[2]),
            requester_id=draft.request.actor.actor_id,
            command_type=draft.request.command.type,
            payload_sha256=draft.payload_sha256,
            preview=draft.preview,
            created_at=draft.created_at,
            expires_at=draft.expires_at,
        )
        return snapshot

    async def store_alert_backtest(
        self, snapshot: AlertBacktestSnapshot
    ) -> AlertBacktestRef:
        window_results = [item.model_dump(mode="json") for item in snapshot.windows]
        evidence_sha256 = hashlib.sha256(
            canonical_json_bytes({"windows": window_results})
        ).hexdigest()
        eligible = sum(item.normalized_value is not None for item in snapshot.windows)
        coverage = (
            Decimal(eligible) / Decimal(len(snapshot.windows))
            if snapshot.windows
            else Decimal(0)
        ).quantize(Decimal("0.000001"))

        async def store(connection: Any) -> tuple[object, ...]:
            cursor = await connection.execute(
                "SELECT * FROM trusted_schema.store_alert_backtest("
                + ", ".join(["%s"] * 7)
                + ")",
                (
                    snapshot.backtest_ref.backtest_id,
                    snapshot.backtest_ref.rule_spec_sha256,
                    evidence_sha256,
                    Jsonb(window_results),
                    coverage,
                    self._scenario_id,
                    snapshot.completed_at,
                ),
            )
            row = await cursor.fetchone()
            if row is None or len(row) != 2:
                raise OperationInfrastructureError(
                    "operation_result_shape_invalid", retryable=False
                )
            if (row[0], row[1]) != (
                snapshot.backtest_ref.backtest_id,
                snapshot.backtest_ref.rule_spec_sha256,
            ):
                raise OperationConflictError(
                    "backtest_identity_conflict", retryable=False
                )
            return row

        row = await _run_transaction(
            dsn=self._proposal_dsn,
            identity=_PROPOSAL_IDENTITY,
            connect=self._connect,
            operation=store,
        )
        stored = AlertBacktestRef(backtest_id=row[0], rule_spec_sha256=row[1])
        return stored

    async def read_proposal(self, proposal_ref: ProposalRef) -> StoredProposal | None:
        async def read(connection: Any) -> tuple[object, ...] | None:
            cursor = await connection.execute(
                "SELECT * FROM trusted_schema.read_operation_proposal(%s, %s, %s)",
                (proposal_ref.proposal_id, proposal_ref.version, self._scenario_id),
            )
            return await cursor.fetchone()

        row = await _run_transaction(
            dsn=self._proposal_dsn,
            identity=_PROPOSAL_IDENTITY,
            connect=self._connect,
            operation=read,
        )
        return None if row is None else _stored_proposal_from_row(row, proposal_ref)

    async def decide_once(
        self,
        receipt: DecisionReceipt,
        approval: StoredApproval | None,
        *,
        now: Any,
    ) -> DecisionReceipt:
        del now
        proposal = await self.read_proposal(receipt.proposal_ref)
        if proposal is None:
            raise ApprovalError("proposal_not_found", retryable=False)
        target_versions_sha256 = hashlib.sha256(
            canonical_json_bytes(proposal.snapshot.preview.target_versions)
        ).hexdigest()
        if receipt.status is ProposalStatus.APPROVED:
            if approval is None or receipt.grant is None:
                raise ApprovalError("approval_required", retryable=False)
            nonce_digest: str | None = approval.nonce_digest
            key_version: int | None = approval.key_version
            grant_expires_at: Any = approval.expires_at
            decision = "approve"
        else:
            nonce_digest = None
            key_version = None
            grant_expires_at = None
            decision = "reject"
        grant_sha256 = (
            execution_grant_sha256(receipt.grant) if receipt.grant is not None else None
        )

        async def write_decision(connection: Any) -> tuple[object, ...]:
            cursor = await connection.execute(
                "SELECT * FROM trusted_schema.record_approval_decision("
                + ", ".join(["%s"] * 13)
                + ")",
                (
                    receipt.proposal_ref.proposal_id,
                    receipt.proposal_ref.version,
                    proposal.requester_id,
                    receipt.approver_id,
                    decision,
                    receipt.reason,
                    nonce_digest,
                    proposal.snapshot.payload_sha256,
                    target_versions_sha256,
                    key_version,
                    grant_expires_at,
                    grant_sha256,
                    self._scenario_id,
                ),
            )
            row = await cursor.fetchone()
            if row is None or len(row) != 4:
                raise OperationInfrastructureError(
                    "operation_result_shape_invalid", retryable=False
                )
            if (row[0], row[1]) != (
                receipt.proposal_ref.proposal_id,
                receipt.proposal_ref.version,
            ):
                raise OperationInfrastructureError(
                    "operation_result_shape_invalid", retryable=False
                )
            return row

        row = await _run_transaction(
            dsn=self._approval_dsn,
            identity=_APPROVAL_IDENTITY,
            connect=self._connect,
            operation=write_decision,
        )
        stored_receipt = receipt.model_copy(
            update={"status": ProposalStatus(row[2]), "decided_at": row[3]}
        )
        return stored_receipt

    async def read_decision(self, proposal_ref: ProposalRef) -> StoredDecision | None:
        async def read(connection: Any) -> tuple[object, ...] | None:
            cursor = await connection.execute(
                "SELECT * FROM trusted_schema.read_approval_decision(%s, %s, %s)",
                (proposal_ref.proposal_id, proposal_ref.version, self._scenario_id),
            )
            return await cursor.fetchone()

        row = await _run_transaction(
            dsn=self._approval_dsn,
            identity=_APPROVAL_IDENTITY,
            connect=self._connect,
            operation=read,
        )
        return None if row is None else _stored_decision_from_row(row, proposal_ref)

    async def read_execution(self, proposal_ref: ProposalRef) -> ExecutionReceipt | None:
        async def read(connection: Any) -> tuple[object, ...] | None:
            cursor = await connection.execute(
                "SELECT execution_id, proposal_id, proposal_version, command_type, "
                "before_version, after_version, committed_at, public_summary, audit_ref "
                "FROM ops.command_execution "
                "WHERE proposal_id = %s AND proposal_version = %s AND status = 'succeeded'",
                (proposal_ref.proposal_id, proposal_ref.version),
            )
            return await cursor.fetchone()

        row = await _run_transaction(
            dsn=self._execution_dsn,
            identity=_EXECUTION_IDENTITY,
            connect=self._connect,
            operation=read,
        )
        if row is None:
            return None
        if len(row) != 9:
            raise OperationInfrastructureError(
                "operation_result_shape_invalid", retryable=False
            )
        if (row[1], row[2]) != (proposal_ref.proposal_id, proposal_ref.version):
            raise OperationInfrastructureError(
                "operation_result_shape_invalid", retryable=False
            )
        return ExecutionReceipt(
            execution_id=row[0],
            proposal_ref=ProposalRef(proposal_id=row[1], version=row[2]),
            status=ExecutionStatus.SUCCEEDED,
            command_type=row[3],
            before_version=row[4],
            after_version=row[5],
            committed_at=row[6],
            public_summary=row[7],
            audit_ref=row[8],
        )

    async def execute_once(
        self,
        proposal: StoredProposal,
        approval: StoredApproval,
        grant: ExecutionGrant,
        *,
        now: Any,
    ) -> ExecutionReceipt:
        del now
        ref = proposal.snapshot.proposal_ref
        execution_id = uuid5(NAMESPACE_URL, f"operation:{ref.proposal_id}:{ref.version}")
        function_name, specific_parameters = _command_call(proposal, ref)
        target_versions_sha256 = hashlib.sha256(
            canonical_json_bytes(grant.target_versions)
        ).hexdigest()
        common_parameters = (
            execution_id,
            ref.proposal_id,
            ref.version,
            grant.approver_id,
            grant.payload_sha256,
            target_versions_sha256,
            approval.nonce_digest,
            f"execute:{ref.proposal_id}:{ref.version}",
            self._scenario_id,
        )

        async def execute(connection: Any) -> tuple[object, ...]:
            parameters = common_parameters + specific_parameters
            cursor = await connection.execute(
                f"SELECT * FROM trusted_schema.{function_name}("
                + ", ".join(["%s"] * len(parameters))
                + ")",
                parameters,
            )
            row = await cursor.fetchone()
            if row is None or len(row) != 7:
                raise OperationInfrastructureError(
                    "operation_result_shape_invalid", retryable=False
                )
            if row[0] != execution_id or row[3] != "operation_succeeded":
                raise OperationInfrastructureError(
                    "operation_result_shape_invalid", retryable=False
                )
            return row

        try:
            row = await _run_transaction(
                dsn=self._execution_dsn,
                identity=_EXECUTION_IDENTITY,
                connect=self._connect,
                operation=execute,
            )
        except OperationConflictError as error:
            if error.reason_code != "operation_identity_conflict":
                raise
            recovered = await self.read_execution(ref)
            if recovered is None:
                raise
            return recovered
        return ExecutionReceipt(
            execution_id=row[0],
            proposal_ref=ref,
            status=ExecutionStatus.SUCCEEDED,
            command_type=proposal.command.type,
            before_version=row[1],
            after_version=row[2],
            committed_at=row[4],
            public_summary=row[5],
            audit_ref=row[6],
        )


class PostgresSellerIdentityStore:
    """Seller identity reads restricted to reviewed proposal-role functions."""

    def __init__(
        self,
        dsn: SecretStr,
        *,
        connect: ConnectionFactory = psycopg.AsyncConnection.connect,
    ) -> None:
        self._dsn = dsn
        self._connect = connect

    async def open(self) -> Self:
        _validate_dsn(self._dsn, _PROPOSAL_IDENTITY)
        return self

    async def find_candidates(
        self, request: SellerTargetRequest
    ) -> tuple[PrivateSellerObservation, ...]:
        async def read(connection: Any) -> list[tuple[object, ...]]:
            cursor = await connection.execute(
                "SELECT * FROM trusted_schema.find_seller_target_candidates("
                + ", ".join(["%s"] * 6)
                + ")",
                (
                    request.metric_ref.name,
                    request.observation_started_at,
                    request.observation_ended_at,
                    request.anomaly_threshold,
                    request.minimum_denominator,
                    list(request.status_filters),
                ),
            )
            return await cursor.fetchall()

        rows = await _run_transaction(
            dsn=self._dsn,
            identity=_PROPOSAL_IDENTITY,
            connect=self._connect,
            operation=read,
            begin_statement="BEGIN READ ONLY",
        )
        try:
            return tuple(
                PrivateSellerObservation(
                    seller_id=str(row[0]),
                    numerator=int(row[1]),
                    denominator=int(row[2]),
                    normalized_value=Decimal(row[3]),
                )
                for row in rows
            )
        except (IndexError, TypeError, ValueError) as error:
            raise OperationInfrastructureError(
                "operation_result_shape_invalid", retryable=False
            ) from error

    async def iter_allowed_identities(self) -> tuple[str, ...]:
        request = SellerTargetRequest(
            metric_ref=MetricRef(
                name="cancellation_rate", revision="identity-enumeration-v1"
            ),
            observation_started_at=datetime.min.replace(tzinfo=UTC),
            observation_ended_at=datetime.max.replace(tzinfo=UTC),
            anomaly_threshold=Decimal(0),
            minimum_denominator=1,
            status_filters=_METRIC_STATUS_FILTERS[AlertMetric.CANCELLATION_RATE],
            evidence_digest="0" * 64,
        )
        observations = await self.find_candidates(request)
        return tuple(item.seller_id for item in observations)


class PostgresAlertObservationStore:
    """Metric observations restricted to reviewed proposal-role functions."""

    def __init__(
        self,
        dsn: SecretStr,
        *,
        connect: ConnectionFactory = psycopg.AsyncConnection.connect,
    ) -> None:
        self._dsn = dsn
        self._connect = connect

    async def open(self) -> Self:
        _validate_dsn(self._dsn, _PROPOSAL_IDENTITY)
        return self

    async def observe(self, query: AlertBacktestQuery) -> tuple[AlertObservation, ...]:
        function_name = _BACKTEST_FUNCTIONS[query.request.metric]
        status_filters = _METRIC_STATUS_FILTERS[query.request.metric]

        async def read(connection: Any) -> list[tuple[object, ...]]:
            cursor = await connection.execute(
                f"SELECT * FROM trusted_schema.{function_name}("
                + ", ".join(["%s"] * 5)
                + ")",
                (
                    query.request.started_at,
                    query.request.ended_at,
                    query.request.window.value,
                    query.request.minimum_denominator,
                    list(status_filters),
                ),
            )
            return await cursor.fetchall()

        rows = await _run_transaction(
            dsn=self._dsn,
            identity=_PROPOSAL_IDENTITY,
            connect=self._connect,
            operation=read,
            begin_statement="BEGIN READ ONLY",
        )
        try:
            return tuple(
                AlertObservation(
                    window_started_at=row[0],
                    window_ended_at=row[1],
                    numerator=int(row[2]),
                    denominator=int(row[3]),
                    complete=True,
                )
                for row in rows
            )
        except (IndexError, TypeError, ValueError) as error:
            raise OperationInfrastructureError(
                "operation_result_shape_invalid", retryable=False
            ) from error
