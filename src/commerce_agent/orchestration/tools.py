"""Closed tool adapters for Retail and the synthetic BirdA harness."""

import json
from collections.abc import Mapping
from hashlib import sha256
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from commerce_agent.context_builder.contracts import PromptStep
from commerce_agent.knowledge.contracts import KnowledgeRequest
from commerce_agent.knowledge.errors import KnowledgeError, KnowledgeInfrastructureError
from commerce_agent.knowledge.module import KnowledgeModule
from commerce_agent.model.contracts import RunScope, ToolCall, ToolResult
from commerce_agent.operations.commands import OperationCommand
from commerce_agent.operations.contracts import (
    ActorContext,
    EvidenceRef,
    ProposalRef,
    ProposalSnapshot,
    ProposeRequest,
)
from commerce_agent.orchestration.contracts import (
    ClarificationRequest,
    InvestigationPlan,
    InvestigationReport,
)
from commerce_agent.query_engine.contracts import QueryRequest
from commerce_agent.query_engine.engine import QueryEngine
from commerce_agent.query_engine.errors import QueryEngineError, QueryInfrastructureError
from commerce_agent.value_resolver.contracts import ValueDomain, ValueResolutionRequest
from commerce_agent.value_resolver.errors import (
    ValueResolutionInfrastructureError,
    ValueResolverError,
)
from commerce_agent.value_resolver.resolver import BusinessValueResolver

_MAX_RESULT_BYTES = 65_536
_SENSITIVE_RESULT_COLUMNS = {
    "order_id",
    "customer_id",
    "customer_unique_id",
    "seller_id",
    "review_id",
    "review_comment_title",
    "review_comment_message",
}
_SENSITIVE_RESULT_SUFFIXES = ("_order_id", "_customer_id", "_seller_id")
_FORBIDDEN_SYNTHETIC_TEXT = (
    "olist",
    "retail",
    "knowledge",
    "resolver",
    "query_engine",
    "postgres",
    "5432",
    "6002",
    "dsn",
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class ToolContractError(RuntimeError):
    """A tool call or public result violates its closed tool contract."""

    def __init__(self, reason_code: str) -> None:
        super().__init__("tool contract validation failed")
        self.reason_code = reason_code
        self.retryable = False


class ToolInfrastructureError(RuntimeError):
    """A tool dependency failed before producing a public result."""

    def __init__(self, reason_code: str) -> None:
        super().__init__("tool infrastructure failed")
        self.reason_code = reason_code
        self.retryable = True


class _KnowledgeArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(min_length=1, max_length=4_000)


class _ResolveArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: ValueDomain
    raw_text: str = Field(min_length=1, max_length=256)


class _SqlArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sql: str = Field(min_length=1, max_length=100_000)


class _SubmittedSqlArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str = Field(pattern=r"^submit_sql_candidate$")
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    sql: str = Field(min_length=1, max_length=100_000)
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")


class _ProposeOperationArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    command: OperationCommand
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=32)
    idempotency_key: str = Field(min_length=8, max_length=128)
    revises: ProposalRef | None = None


class ProposalWorkflowPort(Protocol):
    async def propose(self, request: ProposeRequest) -> ProposalSnapshot: ...


_STEP_TOOLS = {
    PromptStep.RETAIL_DECIDE: frozenset(
        {
            "retrieve_retail_knowledge",
            "resolve_business_value",
            "request_clarification",
            "submit_investigation_plan",
        }
    ),
    PromptStep.RETAIL_CLARIFY: frozenset({"request_clarification"}),
    PromptStep.RETAIL_SQL_GENERATE: frozenset({"submit_sql_candidate"}),
    PromptStep.RETAIL_SQL_REPAIR: frozenset({"submit_sql_candidate"}),
    PromptStep.RETAIL_REPORT: frozenset(
        {"propose_operation", "submit_investigation_report"}
    ),
}


class _SyntheticSchemaArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_ref: str = Field(min_length=1, max_length=512)


class _SyntheticSchemaFixture(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    columns: tuple[str, ...] = Field(min_length=1, max_length=128)
    table: str = Field(min_length=1, max_length=128)


class _SyntheticQueryFixture(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    columns: tuple[str, ...] = Field(max_length=128)
    rows: tuple[dict[str, object], ...] = Field(max_length=1_000)


def _parse_arguments(model: type[BaseModel], call: ToolCall) -> BaseModel:
    try:
        return model.model_validate_json(call.arguments_json)
    except ValidationError as error:
        if any(item.get("type") == "json_invalid" for item in error.errors()):
            raise ToolContractError("malformed_arguments_json") from error
        raise ToolContractError("invalid_tool_arguments") from error


class SyntheticBirdAToolPort:
    """Execute only deterministic cases supplied by a synthetic BirdA fixture."""

    def __init__(
        self,
        *,
        schemas: Mapping[str, ToolResult],
        queries: Mapping[str, ToolResult],
    ) -> None:
        self._schemas = MappingProxyType(dict(schemas))
        self._queries = MappingProxyType(dict(queries))

    @classmethod
    def from_fixture(cls, fixture: Mapping[str, object]) -> "SyntheticBirdAToolPort":
        if set(fixture) != {"revision", "schemas", "queries"}:
            raise ToolContractError("invalid_synthetic_manifest")
        if fixture.get("revision") != "bird-a-runtime-fixture-v1":
            raise ToolContractError("invalid_synthetic_manifest")
        try:
            fixture_text = _canonical_json(dict(fixture)).casefold()
        except (TypeError, ValueError) as error:
            raise ToolContractError("invalid_synthetic_manifest") from error
        if any(token in fixture_text for token in _FORBIDDEN_SYNTHETIC_TEXT):
            raise ToolContractError("invalid_synthetic_manifest")
        raw_schemas = fixture.get("schemas")
        raw_queries = fixture.get("queries")
        if not isinstance(raw_schemas, Mapping) or not isinstance(raw_queries, Mapping):
            raise ToolContractError("invalid_synthetic_manifest")

        schemas: dict[str, ToolResult] = {}
        queries: dict[str, ToolResult] = {}
        try:
            for schema_ref, raw_schema in raw_schemas.items():
                if not isinstance(schema_ref, str) or not schema_ref.startswith("synthetic:"):
                    raise TypeError
                schema = _SyntheticSchemaFixture.model_validate(raw_schema)
                if any(not column or len(column) > 128 for column in schema.columns):
                    raise ValueError
                call = ToolCall(
                    call_id="fixture",
                    name="synthetic_bird_a_observe_schema",
                    arguments_json=_canonical_json({"schema_ref": schema_ref}),
                )
                content = schema.model_dump(mode="json")
                if len(_canonical_json(content).encode("utf-8")) > _MAX_RESULT_BYTES:
                    raise ValueError
                schemas[schema_ref] = cls._result(
                    call,
                    content,
                    (schema_ref,),
                )
            for sql, raw_query in raw_queries.items():
                if not isinstance(sql, str) or "synthetic_" not in sql.casefold():
                    raise TypeError
                query = _SyntheticQueryFixture.model_validate(raw_query)
                normalized_columns = tuple(column.casefold() for column in query.columns)
                expected_columns = set(query.columns)
                if (
                    len(normalized_columns) != len(set(normalized_columns))
                    or any(not column or len(column) > 128 for column in query.columns)
                    or any(set(row) != expected_columns for row in query.rows)
                ):
                    raise ValueError
                content = query.model_dump(mode="json")
                if len(_canonical_json(content).encode("utf-8")) > _MAX_RESULT_BYTES:
                    raise ValueError
                digest = sha256(_canonical_json(content).encode("utf-8")).hexdigest()
                call = ToolCall(
                    call_id="fixture",
                    name="synthetic_bird_a_execute_readonly_sql",
                    arguments_json=_canonical_json({"sql": sql}),
                )
                queries[sql] = cls._result(
                    call,
                    content,
                    (f"synthetic-query:{digest}",),
                )
        except (TypeError, ValidationError, ValueError) as error:
            raise ToolContractError("invalid_synthetic_manifest") from error
        return cls(schemas=schemas, queries=queries)

    async def execute(self, call: ToolCall) -> ToolResult:
        if call.name == "synthetic_bird_a_observe_schema":
            arguments = _parse_arguments(_SyntheticSchemaArguments, call)
            result = self._schemas.get(arguments.schema_ref)
        elif call.name == "synthetic_bird_a_execute_readonly_sql":
            arguments = _parse_arguments(_SqlArguments, call)
            result = self._queries.get(arguments.sql)
        else:
            raise ToolContractError("tool_not_registered")
        if result is None:
            raise ToolContractError("synthetic_fixture_not_found")
        return result.model_copy(update={"call_id": call.call_id})

    @staticmethod
    def _result(
        call: ToolCall,
        content: object,
        source_refs: tuple[str, ...],
    ) -> ToolResult:
        content_json = _canonical_json(content)
        content_sha256 = sha256(content_json.encode("utf-8")).hexdigest()
        return ToolResult(
            call_id=call.call_id,
            name=call.name,
            status="success",
            content_json=content_json,
            deterministic_summary=_canonical_json(
                {"content_sha256": content_sha256, "source_refs": source_refs}
            ),
            content_sha256=content_sha256,
            source_refs=source_refs,
        )


class RetailToolDispatcher:
    """Validate and dispatch only step-scoped Retail tools."""

    def __init__(
        self,
        *,
        knowledge: KnowledgeModule | None,
        resolver: BusinessValueResolver | None,
        query_engine: QueryEngine | None,
        actor: ActorContext | None = None,
        workflow: ProposalWorkflowPort | None = None,
    ) -> None:
        self._knowledge = knowledge
        self._resolver = resolver
        self._query_engine = query_engine
        self._actor = actor
        self._workflow = workflow

    async def execute(
        self,
        scope: RunScope,
        attempt_id: UUID,
        call: ToolCall,
        *,
        step: PromptStep | None = None,
    ) -> ToolResult:
        del attempt_id
        if (scope.track, scope.mode) != ("retail", "retail"):
            raise ToolContractError("retail_scope_required")
        if step is not None and call.name not in _STEP_TOOLS[step]:
            raise ToolContractError("tool_not_allowed_for_step")
        if call.name == "retrieve_retail_knowledge":
            if self._knowledge is None:
                raise ToolInfrastructureError("knowledge_unavailable")
            arguments = _parse_arguments(_KnowledgeArguments, call)
            try:
                bundle = await self._knowledge.retrieve(
                    KnowledgeRequest(question=arguments.question)
                )
            except KnowledgeInfrastructureError as error:
                raise ToolInfrastructureError(error.reason_code) from error
            except KnowledgeError as error:
                return self._error_result(call, error)
            return self._result(
                call,
                bundle.model_dump(mode="json"),
                {
                    "catalog_revision": bundle.catalog_revision,
                    "evidence_count": len(bundle.evidence),
                    "strategy": bundle.strategy,
                },
                tuple(f"knowledge:{item.doc_id}:{item.revision}" for item in bundle.evidence),
            )
        if call.name == "resolve_business_value":
            if self._resolver is None:
                raise ToolInfrastructureError("resolver_unavailable")
            arguments = _parse_arguments(_ResolveArguments, call)
            try:
                resolution = await self._resolver.resolve(
                    ValueResolutionRequest(
                        domain=arguments.domain,
                        raw_text=arguments.raw_text,
                    )
                )
            except ValueResolutionInfrastructureError as error:
                raise ToolInfrastructureError(error.reason_code) from error
            except ValueResolverError as error:
                return self._error_result(call, error)
            return self._result(
                call,
                resolution.model_dump(mode="json"),
                {
                    "candidate_count": len(resolution.candidates),
                    "catalog_revision": resolution.catalog_revision,
                    "domain": resolution.domain,
                    "status": resolution.status,
                },
                tuple(candidate.evidence_ref for candidate in resolution.candidates),
            )
        if call.name == "execute_readonly_sql":
            if self._query_engine is None:
                raise ToolInfrastructureError("query_engine_unavailable")
            arguments = _parse_arguments(_SqlArguments, call)
            try:
                query_result = await self._query_engine.execute(QueryRequest(sql=arguments.sql))
            except QueryInfrastructureError as error:
                raise ToolInfrastructureError(error.reason_code) from error
            except QueryEngineError as error:
                return self._error_result(call, error)
            self._validate_query_result(query_result.columns, query_result.rows)
            return self._result(
                call,
                query_result.model_dump(mode="json"),
                {
                    "columns": query_result.columns,
                    "row_count": query_result.row_count,
                    "truncated": query_result.truncated,
                },
                (f"query:{sha256(arguments.sql.encode('utf-8')).hexdigest()}",),
            )
        if call.name == "request_clarification":
            arguments = _parse_arguments(ClarificationRequest, call)
            return self._result(
                call,
                arguments.model_dump(mode="json"),
                {"item_count": len(arguments.items), "status": "needs_input"},
                (),
            )
        if call.name == "submit_investigation_plan":
            arguments = _parse_arguments(InvestigationPlan, call)
            return self._result(
                call,
                arguments.model_dump(mode="json"),
                {"status": "accepted", "step_count": len(arguments.steps)},
                arguments.evidence_refs,
            )
        if call.name == "submit_sql_candidate":
            arguments = _parse_arguments(_SubmittedSqlArguments, call)
            return self._result(
                call,
                arguments.model_dump(mode="json"),
                {"status": "accepted", "step_id": arguments.step_id},
                arguments.evidence_refs,
            )
        if call.name == "propose_operation":
            if self._actor is None or self._workflow is None:
                raise ToolInfrastructureError("operation_workflow_unavailable")
            arguments = _parse_arguments(_ProposeOperationArguments, call)
            snapshot = await self._workflow.propose(
                ProposeRequest(
                    actor=self._actor,
                    command=arguments.command,
                    evidence_refs=arguments.evidence_refs,
                    idempotency_key=arguments.idempotency_key,
                    revises=arguments.revises,
                )
            )
            source_ref = (
                f"proposal:{snapshot.proposal_ref.proposal_id}:"
                f"{snapshot.proposal_ref.version}"
            )
            return self._result(
                call,
                snapshot.model_dump(mode="json"),
                {"proposal_status": snapshot.status, "status": "accepted"},
                (source_ref,),
            )
        if call.name == "submit_investigation_report":
            arguments = _parse_arguments(InvestigationReport, call)
            return self._result(
                call,
                arguments.model_dump(mode="json"),
                {"claim_count": len(arguments.claims), "status": "completed"},
                tuple(item.evidence_id for item in arguments.available_evidence),
            )
        raise ToolContractError("unknown_tool")

    @staticmethod
    def _validate_query_result(
        columns: list[str],
        rows: list[dict[str, object]],
    ) -> None:
        normalized = tuple(column.casefold() for column in columns)
        if len(normalized) != len(set(normalized)):
            raise ToolContractError("invalid_query_result_shape")
        if any(
            column in _SENSITIVE_RESULT_COLUMNS or column.endswith(_SENSITIVE_RESULT_SUFFIXES)
            for column in normalized
        ):
            raise ToolContractError("sensitive_result_column")
        expected = set(columns)
        if any(set(row) != expected for row in rows):
            raise ToolContractError("invalid_query_result_shape")

    @staticmethod
    def _result(
        call: ToolCall,
        content: object,
        summary: object,
        source_refs: tuple[str, ...],
    ) -> ToolResult:
        content_json = _canonical_json(content)
        if len(content_json.encode("utf-8")) > _MAX_RESULT_BYTES:
            raise ToolContractError("tool_result_too_large")
        content_sha256 = sha256(content_json.encode("utf-8")).hexdigest()
        if isinstance(summary, dict):
            summary = summary | {"content_sha256": content_sha256}
        return ToolResult(
            call_id=call.call_id,
            name=call.name,
            status="success",
            content_json=content_json,
            deterministic_summary=_canonical_json(summary),
            content_sha256=content_sha256,
            source_refs=source_refs,
        )

    @classmethod
    def _error_result(cls, call: ToolCall, error: RuntimeError) -> ToolResult:
        reason_code = getattr(error, "reason_code", "tool_execution_failed")
        content = {"reason_code": reason_code, "status": "error"}
        content_json = _canonical_json(content)
        digest = sha256(content_json.encode("utf-8")).hexdigest()
        return ToolResult(
            call_id=call.call_id,
            name=call.name,
            status="error",
            content_json=content_json,
            deterministic_summary=_canonical_json(content | {"content_sha256": digest}),
            content_sha256=digest,
            source_refs=(),
            error_class=type(error).__name__,
        )
