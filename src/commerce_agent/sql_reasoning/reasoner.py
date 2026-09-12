"""Constrained model-to-SQL generation without database capability."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Protocol

import sqlglot
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlglot import ErrorLevel, exp, parse_one
from sqlglot.errors import ParseError

from commerce_agent.context_builder.contracts import (
    ContextRequest,
    PromptStep,
    TypedErrorDatum,
)
from commerce_agent.context_builder.profiles import ProfileRegistry
from commerce_agent.model.contracts import ModelGateway, ToolCallOutput
from commerce_agent.sql_reasoning.contracts import (
    SqlCandidate,
    SqlFingerprint,
    SqlReasoningRequest,
)
from commerce_agent.sql_reasoning.errors import SqlNoProgress, SqlReasoningContractError


class ContextBuilderPort(Protocol):
    def build(self, request: ContextRequest): ...


class _SubmittedSqlCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str = Field(pattern=r"^submit_sql_candidate$")
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    sql: str = Field(min_length=1, max_length=100_000)
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")


def _fingerprint(kind: str, sql: str) -> SqlFingerprint:
    return SqlFingerprint(
        kind=kind,
        digest=sha256(sql.encode("utf-8")).hexdigest(),
        parser_version=sqlglot.__version__,
    )


def _replace_literal(node: exp.Expression) -> exp.Expression:
    if isinstance(node, exp.Literal):
        if node.is_string:
            return exp.Literal.string("__string_literal__")
        return exp.Literal.number("0")
    if isinstance(node, exp.Boolean):
        return exp.Boolean(this=False)
    return node


def fingerprints(sql: str) -> tuple[SqlFingerprint, SqlFingerprint]:
    try:
        statement = parse_one(sql, read="postgres", error_level=ErrorLevel.RAISE)
        if not isinstance(statement, exp.Query):
            raise SqlReasoningContractError("sql_readonly_required")
        forbidden = (
            exp.Insert,
            exp.Update,
            exp.Delete,
            exp.Create,
            exp.Drop,
            exp.Alter,
            exp.Merge,
            exp.Command,
        )
        if any(statement.find(node_type) is not None for node_type in forbidden):
            raise SqlReasoningContractError("sql_write_intent_forbidden")
        execution = statement.sql(
            dialect="postgres", pretty=False, unsupported_level=ErrorLevel.RAISE
        )
        structural = statement.copy().transform(_replace_literal).sql(
            dialect="postgres", pretty=False, unsupported_level=ErrorLevel.RAISE
        )
    except SqlReasoningContractError:
        raise
    except (ParseError, ValueError, TypeError) as exc:
        raise SqlReasoningContractError("sql_parse_failed") from exc
    return _fingerprint("structural", structural), _fingerprint("execution", execution)


class SqlReasoner:
    def __init__(
        self,
        *,
        context_builder: ContextBuilderPort,
        profiles: ProfileRegistry,
        gateway: ModelGateway,
    ) -> None:
        self._context_builder = context_builder
        self._profiles = profiles
        self._gateway = gateway

    async def generate(self, request: SqlReasoningRequest) -> SqlCandidate:
        latest_error = None
        if request.latest_error is not None:
            message = "The Product database rejected the prior candidate."
            latest_error = TypedErrorDatum(
                error_type="ProductDbError",
                reason_code=request.latest_error.reason_code,
                retryable=request.latest_error.retryable,
                message=message,
                digest=sha256(message.encode()).hexdigest(),
            )
        context_request = ContextRequest(
            run_scope=request.run_scope,
            attempt_id=request.attempt_id,
            sequence=request.sequence,
            profile=self._profiles.get("retail"),
            step=(
                PromptStep.RETAIL_SQL_REPAIR
                if request.latest_error is not None
                else PromptStep.RETAIL_SQL_GENERATE
            ),
            current_input=request.current_input,
            confirmed_facts=request.confirmed_facts,
            evidence=request.evidence,
            latest_error=latest_error,
        )
        bundle = self._context_builder.build(context_request)
        response = await self._gateway.complete(bundle.model_request)
        submitted = self._parse_response(response.output, request)
        structural, execution = fingerprints(submitted.sql)
        candidate = SqlCandidate(
            sql=submitted.sql,
            step_id=submitted.step_id,
            repair_number=request.repair_number,
            evidence_refs=submitted.evidence_refs,
            structural_fingerprint=structural,
            execution_fingerprint=execution,
            usage=response.usage,
            cost=response.cost,
            attempts=response.attempts,
        )
        if request.previous is not None and (
            request.previous.execution_fingerprint == candidate.execution_fingerprint
            and request.previous.evidence_refs == candidate.evidence_refs
        ):
            raise SqlNoProgress("sql_no_progress")
        return candidate

    @staticmethod
    def _parse_response(output, request: SqlReasoningRequest) -> _SubmittedSqlCandidate:
        if not isinstance(output, ToolCallOutput) or len(output.tool_calls) != 1:
            raise SqlReasoningContractError("sql_candidate_output_invalid")
        call = output.tool_calls[0]
        if call.name != "submit_sql_candidate":
            raise SqlReasoningContractError("sql_candidate_tool_invalid")
        try:
            submitted = _SubmittedSqlCandidate.model_validate_json(call.arguments_json)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise SqlReasoningContractError("sql_candidate_payload_invalid") from exc
        if submitted.step_id != request.step_id:
            raise SqlReasoningContractError("sql_step_mismatch")
        allowed_evidence = {item.source_ref for item in request.evidence}
        if not set(submitted.evidence_refs) <= allowed_evidence:
            raise SqlReasoningContractError("sql_evidence_mismatch")
        return submitted
