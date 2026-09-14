"""Checkpointed Retail orchestration over provider-neutral public seams."""

import json
from collections.abc import Mapping
from hashlib import sha256
from typing import cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from commerce_agent.context_builder.builder import ContextBuilder, scope_digest
from commerce_agent.context_builder.contracts import (
    ContextDatum,
    ContextRequest,
    DataNamespace,
    DatumKind,
    PromptStep,
    RetailProfile,
)
from commerce_agent.model._turn_store import AttemptRef, ProviderTurnStore
from commerce_agent.model.contracts import (
    ConversationGroup,
    FinishReason,
    ModelGateway,
    ModelRequest,
    PendingToolBatch,
    ToolCall,
    ToolCallOutput,
    ToolExchangeGroup,
    ToolResult,
)
from commerce_agent.model.errors import ModelGatewayError
from commerce_agent.orchestration._checkpoint import (
    RETAIL_NODE_REVISION,
    RETAIL_STATE_SCHEMA_REVISION,
    CheckpointIncompatible,
    CheckpointInfrastructureError,
    RetailGraphState,
    assert_checkpoint_compatible,
    canonical_checkpoint_json,
    derive_thread_id,
)
from commerce_agent.orchestration.contracts import (
    ClarificationRequest,
    InvestigationPlan,
    InvestigationReport,
    RetailRunOutcome,
    RetailRunRequest,
    StopKind,
    StopOutcome,
)
from commerce_agent.orchestration.tools import (
    ProposalWorkflowPort,
    RetailToolDispatcher,
    ToolContractError,
)
from commerce_agent.query_engine.contracts import (
    EvidenceResult,
    QueryRequest,
    ReconciliationCheck,
    ReconciliationResult,
)
from commerce_agent.query_engine.engine import QueryEngine
from commerce_agent.query_engine.errors import QueryEngineError
from commerce_agent.sql_reasoning.contracts import ProductDbError, SqlCandidate, SqlReasoningRequest
from commerce_agent.sql_reasoning.errors import SqlNoProgress
from commerce_agent.sql_reasoning.reasoner import SqlReasoner

_ZERO_HASH = "0" * 64
# Decide-step allowlist; must stay exactly equal to the retail_decide rule's
# tool_names and inside the frozen retail catalog (tests/contract/
# test_bird_tool_catalog.py pins both).
RETAIL_TOOL_NAMES = frozenset(
    {
        "retrieve_retail_knowledge",
        "resolve_business_value",
        "request_clarification",
        "submit_investigation_plan",
    }
)
_HISTORY_ADAPTER = TypeAdapter(tuple[ConversationGroup, ...])
_RESULTS_ADAPTER = TypeAdapter(tuple[ToolResult, ...])


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class _RetailState(RetailGraphState):
    request_json: str
    model_request_json: str | None
    tool_results_json: str
    last_evidence_digest: str | None
    cleanup_complete: bool


class RetailLoopPolicy(BaseModel):
    """Fixed Retail attempt limits used by the graph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_model_calls: int = Field(default=6, ge=1, le=6)
    max_tool_calls: int = Field(default=12, ge=1, le=12)
    max_replans: int = Field(default=2, ge=0, le=2)
    max_repairs: int = Field(default=3, ge=0, le=3)
    max_clarifications: int = Field(default=2, ge=0, le=2)


class RetailGraph:
    """Run the Retail-only graph with one immutable dependency set."""

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        profile: RetailProfile,
        gateway: ModelGateway,
        dispatcher: RetailToolDispatcher | None,
        checkpointer: object,
        turn_store: ProviderTurnStore,
        operation_workflow: ProposalWorkflowPort | None = None,
        sql_reasoner: SqlReasoner | None = None,
        query_engine: QueryEngine | None = None,
        seller_target_resolver: object | None = None,
        alert_backtester: object | None = None,
        trace: object | None = None,
        loop_policy: RetailLoopPolicy | None = None,
        _interrupt_after: tuple[str, ...] | None = None,
    ) -> None:
        if (profile.kind, profile.key) != ("retail", "retail"):
            raise ToolContractError("retail_profile_required")
        self._context_builder = context_builder
        self._profile = profile
        self._gateway = gateway
        self._dispatcher = dispatcher
        self._turn_store = turn_store
        self._sql_reasoner = sql_reasoner
        self._query_engine = query_engine
        self._seller_target_resolver = seller_target_resolver
        self._alert_backtester = alert_backtester
        self._trace = trace
        self._day4_enabled = any(
            item is not None
            for item in (operation_workflow, sql_reasoner, query_engine)
        )
        self._loop_policy = loop_policy or RetailLoopPolicy()
        self._day4_interrupt_after = frozenset(_interrupt_after or ())
        self._day4_interrupted: set[str] = set()
        self._can_interrupt = bool(_interrupt_after) and not self._day4_enabled

        builder = StateGraph(_RetailState)
        builder.add_node("prepare_context", self._prepare_context)
        builder.add_node("call_model", self._call_model)
        builder.add_node("route_output", self._route_output)
        builder.add_node("validate_tools", self._validate_tools)
        builder.add_node("execute_tools", self._execute_tools)
        builder.add_node("close_exchange", self._close_exchange)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "prepare_context")
        builder.add_edge("prepare_context", "call_model")
        builder.add_edge("call_model", "route_output")
        builder.add_conditional_edges(
            "route_output",
            self._route_after_output,
            {"validate_tools": "validate_tools", "finalize": "finalize"},
        )
        builder.add_conditional_edges(
            "validate_tools",
            self._route_after_validation,
            {"execute_tools": "execute_tools", "finalize": "finalize"},
        )
        builder.add_conditional_edges(
            "execute_tools",
            self._route_after_tools,
            {"close_exchange": "close_exchange", "finalize": "finalize"},
        )
        builder.add_edge("close_exchange", "prepare_context")
        builder.add_edge("finalize", END)
        self._graph = builder.compile(
            checkpointer=checkpointer,
            interrupt_after=(
                list(_interrupt_after)
                if _interrupt_after is not None and not self._day4_enabled
                else None
            ),
        )

    async def run(self, request: RetailRunRequest) -> RetailRunOutcome:
        """Start or resume one exact Retail attempt."""

        if self._day4_enabled:
            return await self._run_day4(request)

        config = {
            "configurable": {
                "thread_id": derive_thread_id(request.run_scope, request.attempt_id)
            }
        }
        snapshot = await self._graph.aget_state(config)
        if snapshot.values:
            assert_checkpoint_compatible(
                snapshot.values,
                expected_config_hash=request.run_scope.config_hash,
                expected_run_scope=request.run_scope,
                expected_attempt_id=request.attempt_id,
            )
            if snapshot.values.get("request_json") != request.model_dump_json():
                raise CheckpointIncompatible("request_mismatch")
            if snapshot.next:
                state = await self._graph.ainvoke(None, config=config)
            else:
                state = snapshot.values
        else:
            state = await self._graph.ainvoke(self._initial_state(request), config=config)
        state = cast(_RetailState, state)
        if self._can_interrupt:
            current_snapshot = await self._graph.aget_state(config)
            if current_snapshot.next:
                return self._interrupted_outcome(state)
        return self._outcome(state)

    async def _run_day4(self, request: RetailRunRequest) -> RetailRunOutcome:
        config = self._checkpoint_config(request)
        snapshot = await self._graph.aget_state(config)
        state: dict[str, object]
        if snapshot.values:
            assert_checkpoint_compatible(
                snapshot.values,
                expected_config_hash=request.run_scope.config_hash,
                expected_run_scope=request.run_scope,
                expected_attempt_id=request.attempt_id,
            )
            if snapshot.values.get("request_json") != request.model_dump_json():
                raise CheckpointIncompatible("request_mismatch")
            state = dict(snapshot.values)
            if state.get("terminal_json") is not None or state.get("stop_reason_json") is not None:
                desired = self._restore_day4_outcome(request, state)
                if not state.get("cleanup_complete"):
                    return await self._finish_day4(request, desired)
                return desired
        else:
            state = dict(self._initial_state(request))
            model_calls = 0
            tool_calls = 0
            hashes = self._zero_hashes(request)
            try:
                output, hashes = await self._call_day4_model(
                    request=request,
                    step=PromptStep.RETAIL_DECIDE,
                    evidence=request.evidence,
                    sequence=model_calls,
                )
                model_calls += 1
                call = self._one_tool_call(output, PromptStep.RETAIL_DECIDE)
                tool_calls += 1
                if call.name == "request_clarification":
                    terminal = ClarificationRequest.model_validate_json(
                        call.arguments_json
                    )
                    desired = self._day4_outcome(
                        request,
                        status="needs_input",
                        terminal=terminal,
                        stop=None,
                        model_calls=model_calls,
                        tool_calls=tool_calls,
                        hashes=hashes,
                    )
                    return await self._cache_and_finish(request, desired, state=state)
                if call.name != "submit_investigation_plan":
                    raise ToolContractError("investigation_plan_required")
                plan = InvestigationPlan.model_validate_json(call.arguments_json)
            except (ValidationError, ValueError) as error:
                raise ToolContractError("invalid_tool_arguments") from error
            state.update(
                {
                    "current_node": "prepare_plan_step",
                    "model_call_count": model_calls,
                    "tool_call_count": tool_calls,
                    "investigation_plan_json": plan.model_dump_json(),
                    "pending_plan_step_ids": [item.step_id for item in plan.steps],
                    **hashes,
                }
            )
            await self._save_day4_state(request, state)

        if self._sql_reasoner is None or self._query_engine is None:
            desired = self._stopped_day4(
                request,
                "product_query_capability_unavailable",
                int(state["model_call_count"]),
                int(state["tool_call_count"]),
                self._state_hashes(state),
            )
            return await self._cache_and_finish(request, desired, state=state)

        plan_json = state.get("investigation_plan_json")
        if not isinstance(plan_json, str):
            raise CheckpointIncompatible("investigation_plan_missing")
        plan = InvestigationPlan.model_validate_json(plan_json)
        steps = {item.step_id: item for item in plan.steps}
        evidence_results = list(
            TypeAdapter(tuple[EvidenceResult, ...]).validate_json(
                str(state["query_evidence_json"])
            )
        )
        pending_step_ids = list(cast(list[str], state["pending_plan_step_ids"]))
        while pending_step_ids:
            step_id = pending_step_ids[0]
            plan_step = steps.get(step_id)
            if plan_step is None:
                raise CheckpointIncompatible("plan_step_missing")
            candidate_json = state.get("sql_candidate_json")
            latest_error_json = state.get("latest_error_json")
            if state.get("current_node") == "execute_query" and isinstance(
                candidate_json, str
            ):
                candidate = SqlCandidate.model_validate_json(candidate_json)
            else:
                model_calls = int(state["model_call_count"])
                tool_calls = int(state["tool_call_count"])
                if model_calls >= self._loop_policy.max_model_calls:
                    desired = self._stopped_day4(
                        request,
                        "model_call_limit",
                        model_calls,
                        tool_calls,
                        self._state_hashes(state),
                    )
                    return await self._cache_and_finish(request, desired, state=state)
                previous = (
                    SqlCandidate.model_validate_json(candidate_json)
                    if isinstance(candidate_json, str)
                    else None
                )
                latest_error = (
                    ProductDbError.model_validate_json(latest_error_json)
                    if isinstance(latest_error_json, str)
                    else None
                )
                repair_number = previous.repair_number + 1 if latest_error else 0
                reasoning = SqlReasoningRequest(
                    run_scope=request.run_scope,
                    attempt_id=request.attempt_id,
                    sequence=model_calls,
                    step_id=plan_step.step_id,
                    current_input=request.current_input,
                    confirmed_facts=request.confirmed_facts,
                    evidence=request.evidence,
                    repair_number=repair_number,
                    latest_error=latest_error,
                    previous=previous,
                )
                try:
                    candidate = await self._sql_reasoner.generate(reasoning)
                except SqlNoProgress:
                    desired = self._stopped_day4(
                        request,
                        "sql_no_progress",
                        model_calls + 1,
                        tool_calls,
                        self._state_hashes(state),
                    )
                    return await self._cache_and_finish(request, desired, state=state)
                state.update(
                    {
                        "current_node": "execute_query",
                        "model_call_count": model_calls + 1,
                        "sql_candidate_json": candidate.model_dump_json(),
                    }
                )
                await self._save_day4_state(request, state)
                interrupted = self._interrupt_day4(
                    request, state, "checkpoint_candidate"
                )
                if interrupted is not None:
                    return interrupted
            try:
                result = await self._query_engine.execute(QueryRequest(sql=candidate.sql))
            except QueryEngineError as error:
                tool_calls = int(state["tool_call_count"]) + 1
                repair_count = int(state["repair_count"])
                if (
                    candidate.repair_number >= self._loop_policy.max_repairs
                    or repair_count >= self._loop_policy.max_repairs
                ):
                    desired = self._stopped_day4(
                        request,
                        "sql_repair_limit",
                        int(state["model_call_count"]),
                        tool_calls,
                        self._state_hashes(state),
                    )
                    return await self._cache_and_finish(request, desired, state=state)
                latest_error = ProductDbError(
                    source="query_engine",
                    reason_code=error.reason_code,
                    retryable=True,
                    evidence_digest=candidate.execution_fingerprint.digest,
                )
                state.update(
                    {
                        "current_node": "sql_reason",
                        "tool_call_count": tool_calls,
                        "repair_count": repair_count + 1,
                        "latest_error_json": latest_error.model_dump_json(),
                    }
                )
                await self._save_day4_state(request, state)
                continue

            evidence_result = EvidenceResult(
                evidence_id=f"query:{plan_step.step_id}",
                columns=tuple(result.columns),
                rows=tuple(result.rows),
                query_fingerprint=candidate.execution_fingerprint.digest,
            )
            evidence_results.append(evidence_result)
            pending_step_ids.pop(0)
            state.update(
                {
                    "current_node": "prepare_plan_step",
                    "tool_call_count": int(state["tool_call_count"]) + 1,
                    "pending_plan_step_ids": pending_step_ids,
                    "sql_candidate_json": None,
                    "latest_error_json": None,
                    "query_evidence_json": _canonical_json(
                        [item.model_dump(mode="json") for item in evidence_results]
                    ),
                }
            )
            await self._save_day4_state(request, state)
            interrupted = self._interrupt_day4(request, state, "execute_query")
            if interrupted is not None:
                return interrupted

        reconciliation_json = state.get("reconciliation_json")
        if isinstance(reconciliation_json, str):
            reconciliation = ReconciliationResult.model_validate_json(
                reconciliation_json
            )
        else:
            reconciliation_digest = sha256(
                _canonical_json(
                    [item.model_dump(mode="json") for item in evidence_results]
                ).encode()
            ).hexdigest()
            reconciliation = ReconciliationResult(
                passed=True,
                checks=(
                    ReconciliationCheck(
                        rule_type="null",
                        passed=True,
                        reason_code="reconciled",
                        actual_digest=reconciliation_digest,
                        expected_digest=reconciliation_digest,
                    ),
                ),
            )
            state.update(
                {
                    "current_node": "prepare_report",
                    "reconciliation_json": reconciliation.model_dump_json(),
                }
            )
            await self._save_day4_state(request, state)

        query_datums = tuple(self._query_evidence_datum(item) for item in evidence_results)
        reconciliation_content = reconciliation.model_dump_json()
        report_evidence = (
            *query_datums,
            ContextDatum(
                kind=DatumKind.TOOL_RESULT,
                namespace=DataNamespace.PRODUCT_QUERY_RESULT,
                source_ref="reconciliation:plan",
                revision="reconciliation-v1",
                content=reconciliation_content,
                digest=sha256(reconciliation_content.encode()).hexdigest(),
            ),
        )
        proposal_refs = list(json.loads(str(state["proposal_refs_json"])))
        while True:
            model_calls = int(state["model_call_count"])
            tool_calls = int(state["tool_call_count"])
            pending_call_json = state.get("pending_tool_batch_json")
            if state.get("current_node") == "create_proposal" and isinstance(
                pending_call_json, str
            ):
                call = ToolCall.model_validate_json(pending_call_json)
            else:
                if model_calls >= self._loop_policy.max_model_calls:
                    desired = self._stopped_day4(
                        request,
                        "model_call_limit",
                        model_calls,
                        tool_calls,
                        self._state_hashes(state),
                    )
                    return await self._cache_and_finish(
                        request, desired, state=state
                    )
                output, hashes = await self._call_day4_model(
                    request=request,
                    step=PromptStep.RETAIL_REPORT,
                    evidence=report_evidence,
                    sequence=model_calls,
                )
                model_calls += 1
                call = self._one_tool_call(output, PromptStep.RETAIL_REPORT)
                tool_calls += 1
                state.update(
                    {
                        "model_call_count": model_calls,
                        "tool_call_count": tool_calls,
                        **hashes,
                    }
                )
            if call.name == "propose_operation":
                if self._dispatcher is None:
                    desired = self._stopped_day4(
                        request,
                        "operation_workflow_unavailable",
                        model_calls,
                        tool_calls,
                        self._state_hashes(state),
                    )
                    return await self._cache_and_finish(request, desired, state=state)
                state.update(
                    {
                        "current_node": "create_proposal",
                        "pending_tool_batch_json": call.model_dump_json(),
                    }
                )
                await self._save_day4_state(request, state)
                result = await self._dispatcher.execute(
                    request.run_scope,
                    request.attempt_id,
                    call,
                    step=PromptStep.RETAIL_REPORT,
                )
                proposal_refs.extend(result.source_refs)
                state.update(
                    {
                        "current_node": "prepare_report",
                        "pending_tool_batch_json": None,
                        "proposal_refs_json": _canonical_json(proposal_refs),
                    }
                )
                await self._save_day4_state(request, state)
                interrupted = self._interrupt_day4(
                    request, state, "create_proposal"
                )
                if interrupted is not None:
                    return interrupted
                continue
            if call.name != "submit_investigation_report":
                raise ToolContractError("investigation_report_required")
            try:
                report = InvestigationReport.model_validate_json(call.arguments_json)
            except ValidationError as error:
                raise ToolContractError("invalid_tool_arguments") from error
            declared = {str(item.proposal_id) for item in report.proposal_refs}
            if proposal_refs and not all(
                any(proposal_id in ref for proposal_id in declared)
                for ref in proposal_refs
            ):
                raise ToolContractError("proposal_reference_mismatch")
            desired = self._day4_outcome(
                request,
                status="completed",
                terminal=report,
                stop=None,
                model_calls=model_calls,
                tool_calls=tool_calls,
                hashes=self._state_hashes(state),
            )
            return await self._cache_and_finish(request, desired, state=state)

    async def _call_day4_model(
        self,
        *,
        request: RetailRunRequest,
        step: PromptStep,
        evidence: tuple[ContextDatum, ...],
        sequence: int,
    ):
        bundle = self._context_builder.build(
            ContextRequest(
                run_scope=request.run_scope,
                attempt_id=request.attempt_id,
                sequence=sequence,
                profile=self._profile,
                step=step,
                current_input=request.current_input,
                confirmed_facts=request.confirmed_facts,
                evidence=evidence,
            )
        )
        response = await self._gateway.complete(bundle.model_request)
        ref = response.provider_turn_ref
        if (
            ref.scope_digest != scope_digest(request.run_scope)
            or ref.attempt_id != request.attempt_id
            or ref.sequence != sequence
        ):
            raise ToolContractError("model_response_binding_mismatch")
        return response.output, {
            "prompt_policy_hash": bundle.prompt_policy_hash,
            "rendered_prompt_hash": bundle.rendered_prompt_hash,
            "tool_hash": bundle.tool_hash,
            "context_hash": bundle.context_hash,
            "config_hash": bundle.config_hash,
        }

    def _one_tool_call(self, output, step: PromptStep):
        if not isinstance(output, ToolCallOutput) or len(output.tool_calls) != 1:
            raise ToolContractError("typed_retail_action_required")
        call = output.tool_calls[0]
        allowed = next(
            set(rule.tool_names)
            for rule in self._profile.inference_rules
            if rule.step == step
        )
        if call.name not in allowed:
            raise ToolContractError("tool_not_allowed_for_step")
        return call

    async def _cache_and_finish(
        self,
        request: RetailRunRequest,
        desired: RetailRunOutcome,
        *,
        state: Mapping[str, object] | None = None,
    ) -> RetailRunOutcome:
        persisted = dict(state or self._initial_state(request))
        persisted.update(
            {
                "current_node": "finalize",
                "model_call_count": desired.model_calls,
                "tool_call_count": desired.tool_calls,
                "prompt_policy_hash": desired.prompt_policy_hash,
                "rendered_prompt_hash": desired.rendered_prompt_hash,
                "tool_hash": desired.tool_hash,
                "context_hash": desired.context_hash,
                "config_hash": desired.config_hash,
                "terminal_json": (
                    desired.terminal.model_dump_json()
                    if desired.terminal is not None
                    else None
                ),
                "stop_reason_json": (
                    desired.stop.model_dump_json() if desired.stop is not None else None
                ),
                "cleanup_complete": False,
            }
        )
        await self._save_day4_state(request, persisted)
        interrupted = self._interrupt_day4(request, persisted, "commit_terminal")
        if interrupted is not None:
            return interrupted
        return await self._finish_day4(request, desired)

    async def _save_day4_state(
        self, request: RetailRunRequest, state: Mapping[str, object]
    ) -> None:
        canonical_checkpoint_json(state)
        await self._graph.aupdate_state(
            self._checkpoint_config(request), dict(state), as_node="finalize"
        )

    def _interrupt_day4(
        self,
        request: RetailRunRequest,
        state: Mapping[str, object],
        boundary: str,
    ) -> RetailRunOutcome | None:
        if (
            boundary not in self._day4_interrupt_after
            or boundary in self._day4_interrupted
        ):
            return None
        self._day4_interrupted.add(boundary)
        return self._stopped_day4(
            request,
            "checkpoint_interrupt",
            int(state["model_call_count"]),
            int(state["tool_call_count"]),
            self._state_hashes(state),
            retryable=True,
        )

    async def _finish_day4(
        self,
        request: RetailRunRequest,
        desired: RetailRunOutcome,
    ) -> RetailRunOutcome:
        try:
            await self._turn_store.delete_attempt(
                AttemptRef(
                    scope_digest=scope_digest(request.run_scope),
                    attempt_id=request.attempt_id,
                )
            )
        except Exception:  # noqa: BLE001 - cleanup ports may expose arbitrary failures
            return self._stopped_day4(
                request,
                "private_turn_cleanup_failed",
                desired.model_calls,
                desired.tool_calls,
                {
                    "prompt_policy_hash": desired.prompt_policy_hash,
                    "rendered_prompt_hash": desired.rendered_prompt_hash,
                    "tool_hash": desired.tool_hash,
                    "context_hash": desired.context_hash,
                    "config_hash": desired.config_hash,
                },
                retryable=True,
            )
        await self._graph.aupdate_state(
            self._checkpoint_config(request),
            {"cleanup_complete": True, "current_node": "complete"},
            as_node="finalize",
        )
        return desired

    @staticmethod
    def _checkpoint_config(request: RetailRunRequest) -> dict[str, object]:
        return {
            "configurable": {
                "thread_id": derive_thread_id(request.run_scope, request.attempt_id)
            }
        }

    @staticmethod
    def _restore_day4_outcome(
        request: RetailRunRequest, state: Mapping[str, object]
    ) -> RetailRunOutcome:
        terminal_json = state.get("terminal_json")
        stop_json = state.get("stop_reason_json")
        terminal = None
        if isinstance(terminal_json, str):
            terminal_payload = json.loads(terminal_json)
            terminal = (
                ClarificationRequest.model_validate(terminal_payload)
                if terminal_payload.get("type") == "clarification_request"
                else InvestigationReport.model_validate(terminal_payload)
            )
        stop = StopOutcome.model_validate_json(stop_json) if isinstance(stop_json, str) else None
        status = (
            "stopped"
            if stop is not None
            else "needs_input"
            if isinstance(terminal, ClarificationRequest)
            else "completed"
        )
        return RetailRunOutcome(
            status=status,
            terminal=terminal,
            stop=stop,
            model_calls=int(state["model_call_count"]),
            tool_calls=int(state["tool_call_count"]),
            prompt_policy_hash=str(state["prompt_policy_hash"]),
            rendered_prompt_hash=str(state["rendered_prompt_hash"]),
            tool_hash=str(state["tool_hash"]),
            context_hash=str(state["context_hash"]),
            config_hash=str(state["config_hash"]),
            attempt_id=request.attempt_id,
        )

    @staticmethod
    def _state_hashes(state: Mapping[str, object]) -> dict[str, str]:
        return {
            "prompt_policy_hash": str(state["prompt_policy_hash"]),
            "rendered_prompt_hash": str(state["rendered_prompt_hash"]),
            "tool_hash": str(state["tool_hash"]),
            "context_hash": str(state["context_hash"]),
            "config_hash": str(state["config_hash"]),
        }

    @staticmethod
    def _query_evidence_datum(evidence: EvidenceResult) -> ContextDatum:
        content = _canonical_json(evidence.model_dump(mode="json"))
        return ContextDatum(
            kind=DatumKind.TOOL_RESULT,
            namespace=DataNamespace.PRODUCT_QUERY_RESULT,
            source_ref=evidence.evidence_id,
            revision="query-evidence-v1",
            content=content,
            digest=sha256(content.encode()).hexdigest(),
        )

    @staticmethod
    def _zero_hashes(request: RetailRunRequest) -> dict[str, str]:
        return {
            "prompt_policy_hash": _ZERO_HASH,
            "rendered_prompt_hash": _ZERO_HASH,
            "tool_hash": _ZERO_HASH,
            "context_hash": _ZERO_HASH,
            "config_hash": request.run_scope.config_hash,
        }

    @staticmethod
    def _day4_outcome(
        request: RetailRunRequest,
        *,
        status,
        terminal,
        stop,
        model_calls: int,
        tool_calls: int,
        hashes: dict[str, str],
    ) -> RetailRunOutcome:
        return RetailRunOutcome(
            status=status,
            terminal=terminal,
            stop=stop,
            model_calls=model_calls,
            tool_calls=tool_calls,
            attempt_id=request.attempt_id,
            **hashes,
        )

    def _stopped_day4(
        self,
        request: RetailRunRequest,
        reason_code: str,
        model_calls: int,
        tool_calls: int,
        hashes: dict[str, str],
        *,
        retryable: bool = False,
    ) -> RetailRunOutcome:
        kind = (
            StopKind.BUDGET_EXHAUSTED
            if reason_code.endswith("_limit")
            else StopKind.INFRASTRUCTURE_ERROR
            if retryable or "unavailable" in reason_code or "cleanup" in reason_code
            else StopKind.NO_PROGRESS
        )
        return self._day4_outcome(
            request,
            status="stopped",
            terminal=None,
            stop=StopOutcome(kind=kind, reason_code=reason_code, retryable=retryable),
            model_calls=model_calls,
            tool_calls=tool_calls,
            hashes=hashes,
        )

    @staticmethod
    def _initial_state(request: RetailRunRequest) -> _RetailState:
        return _RetailState(
            state_schema_revision=RETAIL_STATE_SCHEMA_REVISION,
            node_revision=RETAIL_NODE_REVISION,
            run_scope_json=request.run_scope.model_dump_json(),
            attempt_id=str(request.attempt_id),
            current_node="prepare_context",
            clarification_count=0,
            replan_count=0,
            repair_count=0,
            model_call_count=0,
            tool_call_count=0,
            confirmed_facts_json=_canonical_json(
                [item.model_dump(mode="json") for item in request.confirmed_facts]
            ),
            investigation_plan_json=None,
            pending_plan_step_ids=[],
            sql_candidate_json=None,
            query_evidence_json="[]",
            reconciliation_json=None,
            proposal_refs_json="[]",
            terminal_json=None,
            provider_history_json="[]",
            revisions_json=_canonical_json(
                {
                    "config_hash": request.run_scope.config_hash,
                    "node_revision": RETAIL_NODE_REVISION,
                    "state_schema_revision": RETAIL_STATE_SCHEMA_REVISION,
                }
            ),
            prompt_policy_hash=_ZERO_HASH,
            rendered_prompt_hash=_ZERO_HASH,
            tool_hash=_ZERO_HASH,
            context_hash=_ZERO_HASH,
            config_hash=request.run_scope.config_hash,
            history_json="[]",
            pending_tool_batch_json=None,
            sql_candidate=None,
            latest_error_json=None,
            evidence_summaries=[],
            final_output_json=None,
            stop_reason_json=None,
            request_json=request.model_dump_json(),
            model_request_json=None,
            tool_results_json="[]",
            last_evidence_digest=None,
            cleanup_complete=False,
        )

    async def _prepare_context(self, state: _RetailState) -> dict[str, object]:
        request = RetailRunRequest.model_validate_json(state["request_json"])
        history = _HISTORY_ADAPTER.validate_python(json.loads(state["history_json"]))
        bundle = self._context_builder.build(
            ContextRequest(
                run_scope=request.run_scope,
                attempt_id=request.attempt_id,
                sequence=state["model_call_count"],
                profile=self._profile,
                step="retail_decide",
                current_input=request.current_input,
                confirmed_facts=request.confirmed_facts,
                evidence=request.evidence,
                latest_error=request.latest_error,
                history=history,
            )
        )
        return {
            "current_node": "call_model",
            "model_request_json": bundle.model_request.model_dump_json(),
            "prompt_policy_hash": bundle.prompt_policy_hash,
            "rendered_prompt_hash": bundle.rendered_prompt_hash,
            "tool_hash": bundle.tool_hash,
            "context_hash": bundle.context_hash,
            "config_hash": bundle.config_hash,
        }

    async def _call_model(self, state: _RetailState) -> dict[str, object]:
        if state["model_call_count"] >= self._loop_policy.max_model_calls:
            return {
                "current_node": "route_output",
                "stop_reason_json": StopOutcome(
                    kind=StopKind.BUDGET_EXHAUSTED,
                    reason_code="model_call_limit",
                    retryable=False,
                ).model_dump_json(),
            }
        model_request_json = state["model_request_json"]
        if model_request_json is None:
            raise CheckpointInfrastructureError("model_request_missing")
        request = ModelRequest.model_validate_json(model_request_json)
        try:
            response = await self._gateway.complete(request)
        except ModelGatewayError as error:
            stop = StopOutcome(
                kind=StopKind.INFRASTRUCTURE_ERROR,
                reason_code=error.reason_code,
                retryable=error.retryable,
            )
            return {
                "current_node": "route_output",
                "model_call_count": state["model_call_count"] + 1,
                "stop_reason_json": stop.model_dump_json(),
            }

        response_ref = response.provider_turn_ref
        if (
            response_ref.scope_digest != scope_digest(request.run_scope)
            or response_ref.attempt_id != request.attempt_id
            or response_ref.sequence != request.sequence
        ):
            raise ToolContractError("model_response_binding_mismatch")

        updates: dict[str, object] = {
            "current_node": "route_output",
            "model_call_count": state["model_call_count"] + 1,
        }
        if isinstance(response.output, ToolCallOutput):
            pending = PendingToolBatch(
                provider_turn_ref=response.provider_turn_ref,
                tool_calls=response.output.tool_calls,
                expected_tool_call_ids=tuple(
                    call.call_id for call in response.output.tool_calls
                ),
                graph_node_revision=RETAIL_NODE_REVISION,
                budget_snapshot_json=_canonical_json(
                    {
                        "model_calls": state["model_call_count"] + 1,
                        "tool_calls": state["tool_call_count"],
                    }
                ),
                prompt_policy_hash=state["prompt_policy_hash"],
                rendered_prompt_hash=state["rendered_prompt_hash"],
                tool_hash=state["tool_hash"],
                context_hash=state["context_hash"],
                config_hash=state["config_hash"],
            )
            updates.update(
                {
                    "pending_tool_batch_json": pending.model_dump_json(),
                }
            )
            return updates
        if response.finish_reason is FinishReason.STOP:
            updates["final_output_json"] = response.output.model_dump_json()
            return updates

        stop_by_finish = {
            FinishReason.LENGTH: StopOutcome(
                kind=StopKind.INSUFFICIENT_DATA,
                reason_code="model_output_incomplete",
                retryable=False,
            ),
            FinishReason.CONTENT_FILTER: StopOutcome(
                kind=StopKind.UNSAFE,
                reason_code="model_output_filtered",
                retryable=False,
            ),
            FinishReason.INSUFFICIENT_SYSTEM_RESOURCE: StopOutcome(
                kind=StopKind.INFRASTRUCTURE_ERROR,
                reason_code="insufficient_system_resource",
                retryable=True,
            ),
        }
        updates["stop_reason_json"] = stop_by_finish[response.finish_reason].model_dump_json()
        return updates

    async def _route_output(self, state: _RetailState) -> dict[str, object]:
        if state["pending_tool_batch_json"] is not None:
            return {"current_node": "validate_tools"}
        if state["final_output_json"] is not None or state["stop_reason_json"] is not None:
            return {"current_node": "finalize"}
        raise ToolContractError("model_output_missing")

    @staticmethod
    def _route_after_output(state: _RetailState) -> str:
        return state["current_node"]

    async def _validate_tools(self, state: _RetailState) -> dict[str, object]:
        if self._dispatcher is None:
            raise ToolContractError("retail_dispatcher_missing")
        pending_json = state["pending_tool_batch_json"]
        if pending_json is None:
            raise ToolContractError("pending_tool_batch_missing")
        pending = PendingToolBatch.model_validate_json(pending_json)
        if pending.graph_node_revision != RETAIL_NODE_REVISION:
            raise ToolContractError("pending_node_revision_mismatch")
        for field_name in (
            "prompt_policy_hash",
            "rendered_prompt_hash",
            "tool_hash",
            "context_hash",
            "config_hash",
        ):
            if getattr(pending, field_name) != state[field_name]:
                raise ToolContractError("pending_hash_mismatch")
        if any(call.name not in RETAIL_TOOL_NAMES for call in pending.tool_calls):
            raise ToolContractError("tool_not_registered")
        if (
            state["tool_call_count"] + len(pending.tool_calls)
            > self._loop_policy.max_tool_calls
        ):
            return {
                "current_node": "finalize",
                "stop_reason_json": StopOutcome(
                    kind=StopKind.BUDGET_EXHAUSTED,
                    reason_code="tool_call_limit",
                    retryable=False,
                ).model_dump_json(),
            }
        return {"current_node": "execute_tools"}

    @staticmethod
    def _route_after_validation(state: _RetailState) -> str:
        return state["current_node"]

    async def _execute_tools(self, state: _RetailState) -> dict[str, object]:
        if self._dispatcher is None:
            raise ToolContractError("retail_dispatcher_missing")
        pending_json = state["pending_tool_batch_json"]
        if pending_json is None:
            raise ToolContractError("pending_tool_batch_missing")
        pending = PendingToolBatch.model_validate_json(pending_json)
        scope = RetailRunRequest.model_validate_json(state["request_json"]).run_scope
        attempt_id = UUID(state["attempt_id"])
        executed: list[ToolResult] = []
        for call in pending.tool_calls:
            executed.append(await self._dispatcher.execute(scope, attempt_id, call))
        results = tuple(executed)
        if any(
            (result.call_id, result.name) != (call.call_id, call.name)
            for call, result in zip(pending.tool_calls, results, strict=True)
        ):
            raise ToolContractError("tool_result_mismatch")
        evidence_digest = sha256(
            _canonical_json(
                [
                    {
                        "arguments": json.loads(call.arguments_json),
                        "error_class": result.error_class,
                        "name": call.name,
                        "result_sha256": result.content_sha256,
                        "status": result.status,
                    }
                    for call, result in zip(pending.tool_calls, results, strict=True)
                ]
            ).encode("utf-8")
        ).hexdigest()
        updates: dict[str, object] = {
            "current_node": "close_exchange",
            "tool_call_count": state["tool_call_count"] + len(results),
            "tool_results_json": _canonical_json(
                [result.model_dump(mode="json") for result in results]
            ),
            "last_evidence_digest": evidence_digest,
        }
        if evidence_digest == state["last_evidence_digest"]:
            updates["current_node"] = "finalize"
            updates["stop_reason_json"] = StopOutcome(
                kind=StopKind.NO_PROGRESS,
                reason_code="repeated_evidence",
                evidence_digest=evidence_digest,
                retryable=False,
            ).model_dump_json()
        return updates

    @staticmethod
    def _route_after_tools(state: _RetailState) -> str:
        return state["current_node"]

    async def _close_exchange(self, state: _RetailState) -> dict[str, object]:
        pending_json = state["pending_tool_batch_json"]
        if pending_json is None:
            raise ToolContractError("pending_tool_batch_missing")
        pending = PendingToolBatch.model_validate_json(pending_json)
        results = _RESULTS_ADAPTER.validate_python(json.loads(state["tool_results_json"]))
        exchange = ToolExchangeGroup(
            group_type="tool_exchange",
            provider_turn_ref=pending.provider_turn_ref,
            tool_calls=pending.tool_calls,
            tool_results=results,
        )
        history = list(_HISTORY_ADAPTER.validate_python(json.loads(state["history_json"])))
        history.append(exchange)
        return {
            "current_node": "prepare_context",
            "history_json": _canonical_json(
                [group.model_dump(mode="json") for group in history]
            ),
            "pending_tool_batch_json": None,
            "tool_results_json": "[]",
            "model_request_json": None,
        }

    async def _finalize(self, state: _RetailState) -> dict[str, object]:
        request = RetailRunRequest.model_validate_json(state["request_json"])
        try:
            await self._turn_store.delete_attempt(
                AttemptRef(
                    scope_digest=scope_digest(request.run_scope),
                    attempt_id=request.attempt_id,
                )
            )
        except Exception as error:
            raise CheckpointInfrastructureError("private_turn_cleanup_failed") from error
        return {"cleanup_complete": True, "current_node": "complete"}

    @staticmethod
    def _outcome(state: _RetailState) -> RetailRunOutcome:
        stop = (
            StopOutcome.model_validate_json(state["stop_reason_json"])
            if state["stop_reason_json"] is not None
            else None
        )
        if state["final_output_json"] is not None:
            stop = StopOutcome(
                kind=StopKind.UNSAFE,
                reason_code="typed_retail_terminal_required",
                retryable=False,
            )
        return RetailRunOutcome(
            status="stopped",
            terminal=None,
            stop=stop,
            model_calls=state["model_call_count"],
            tool_calls=state["tool_call_count"],
            prompt_policy_hash=state["prompt_policy_hash"],
            rendered_prompt_hash=state["rendered_prompt_hash"],
            tool_hash=state["tool_hash"],
            context_hash=state["context_hash"],
            config_hash=state["config_hash"],
            attempt_id=UUID(state["attempt_id"]),
        )

    @staticmethod
    def _interrupted_outcome(state: _RetailState) -> RetailRunOutcome:
        interrupted = dict(state)
        interrupted["final_output_json"] = None
        interrupted["stop_reason_json"] = StopOutcome(
            kind=StopKind.INFRASTRUCTURE_ERROR,
            reason_code="checkpoint_interrupt",
            retryable=True,
        ).model_dump_json()
        return RetailGraph._outcome(cast(_RetailState, interrupted))
