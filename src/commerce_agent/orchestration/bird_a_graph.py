"""Attempt-scoped active loop for the synthetic BirdA track."""

import json
from hashlib import sha256

from commerce_agent.context_builder.builder import ContextBuilder, scope_digest
from commerce_agent.context_builder.contracts import BirdAProfile, ContextRequest, PromptBundle
from commerce_agent.model._turn_store import AttemptRef, ProviderTurnStore
from commerce_agent.model.contracts import (
    FinalOutput,
    FinishReason,
    ModelGateway,
    ToolCallOutput,
    ToolExchangeGroup,
    ToolResult,
)
from commerce_agent.model.errors import ModelGatewayError
from commerce_agent.orchestration.contracts import (
    BirdARunOutcome,
    BirdARunRequest,
    StopKind,
    StopOutcome,
)
from commerce_agent.orchestration.tools import (
    BirdToolPort,
    ToolContractError,
    ToolInfrastructureError,
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class BirdAGraph:
    """Run one non-resumable BirdA attempt against the injected BirdToolPort."""

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        profile: BirdAProfile,
        gateway: ModelGateway,
        tool_port: BirdToolPort,
        turn_store: ProviderTurnStore,
    ) -> None:
        if (profile.kind, profile.key) != ("bird_a", "bird_a"):
            raise ToolContractError("bird_a_profile_required")
        self._context_builder = context_builder
        self._profile = profile
        self._gateway = gateway
        self._tool_port = tool_port
        self._turn_store = turn_store

    async def run(self, request: BirdARunRequest) -> BirdARunOutcome:
        """Run one attempt and destroy its provider-private state on exit."""

        try:
            return await self._run_attempt(request)
        finally:
            try:
                await self._turn_store.delete_attempt(
                    AttemptRef(
                        scope_digest=scope_digest(request.run_scope),
                        attempt_id=request.attempt_id,
                    )
                )
            except Exception as error:
                raise ToolInfrastructureError("private_turn_cleanup_failed") from error

    async def _run_attempt(self, request: BirdARunRequest) -> BirdARunOutcome:
        """Run the active model/tool loop without retaining resumable state."""

        history: list[ToolExchangeGroup] = []
        model_calls = 0
        tool_calls = 0
        last_evidence_digest: str | None = None
        bundle: PromptBundle | None = None
        while True:
            if model_calls >= request.max_model_calls:
                if bundle is None:
                    raise ToolContractError("prompt_bundle_missing")
                return self._stopped(
                    request,
                    bundle,
                    StopOutcome(
                        kind=StopKind.BUDGET_EXHAUSTED,
                        reason_code="model_call_limit",
                        retryable=False,
                    ),
                    model_calls=model_calls,
                    tool_calls=tool_calls,
                )
            bundle = self._context_builder.build(
                ContextRequest(
                    run_scope=request.run_scope,
                    attempt_id=request.attempt_id,
                    sequence=model_calls,
                    profile=self._profile,
                    step="bird_a_act",
                    current_input=request.current_input,
                    confirmed_facts=request.confirmed_facts,
                    evidence=request.evidence,
                    latest_error=request.latest_error,
                    history=tuple(history),
                )
            )
            try:
                response = await self._gateway.complete(bundle.model_request)
            except ModelGatewayError as error:
                return self._stopped(
                    request,
                    bundle,
                    StopOutcome(
                        kind=StopKind.INFRASTRUCTURE_ERROR,
                        reason_code=error.reason_code,
                        retryable=error.retryable,
                    ),
                    model_calls=model_calls + 1,
                    tool_calls=tool_calls,
                )
            self._validate_response_binding(bundle, response.provider_turn_ref)
            model_calls += 1

            if isinstance(response.output, ToolCallOutput):
                if tool_calls + len(response.output.tool_calls) > request.max_tool_calls:
                    return self._stopped(
                        request,
                        bundle,
                        StopOutcome(
                            kind=StopKind.BUDGET_EXHAUSTED,
                            reason_code="tool_call_limit",
                            retryable=False,
                        ),
                        model_calls=model_calls,
                        tool_calls=tool_calls,
                    )
                results = tuple(
                    [await self._tool_port.execute(call) for call in response.output.tool_calls]
                )
                tool_calls += len(results)
                evidence_digest = _evidence_digest(response.output, results)
                if evidence_digest == last_evidence_digest:
                    return self._stopped(
                        request,
                        bundle,
                        StopOutcome(
                            kind=StopKind.NO_PROGRESS,
                            reason_code="repeated_evidence",
                            evidence_digest=evidence_digest,
                            retryable=False,
                        ),
                        model_calls=model_calls,
                        tool_calls=tool_calls,
                    )
                last_evidence_digest = evidence_digest
                history.append(
                    ToolExchangeGroup(
                        group_type="tool_exchange",
                        provider_turn_ref=response.provider_turn_ref,
                        tool_calls=response.output.tool_calls,
                        tool_results=results,
                    )
                )
                continue
            if response.finish_reason is not FinishReason.STOP:
                if response.finish_reason is FinishReason.CONTENT_FILTER:
                    return self._stopped(
                        request,
                        bundle,
                        StopOutcome(
                            kind=StopKind.UNSAFE,
                            reason_code="model_output_filtered",
                            retryable=False,
                        ),
                        model_calls=model_calls,
                        tool_calls=tool_calls,
                    )
                if response.finish_reason is FinishReason.LENGTH:
                    return self._stopped(
                        request,
                        bundle,
                        StopOutcome(
                            kind=StopKind.INSUFFICIENT_DATA,
                            reason_code="model_output_incomplete",
                            retryable=False,
                        ),
                        model_calls=model_calls,
                        tool_calls=tool_calls,
                    )
                if response.finish_reason is FinishReason.INSUFFICIENT_SYSTEM_RESOURCE:
                    return self._stopped(
                        request,
                        bundle,
                        StopOutcome(
                            kind=StopKind.INFRASTRUCTURE_ERROR,
                            reason_code="insufficient_system_resource",
                            retryable=True,
                        ),
                        model_calls=model_calls,
                        tool_calls=tool_calls,
                    )
                raise ToolContractError("unsupported_model_finish_reason")
            return self._completed(
                request,
                bundle,
                response.output,
                model_calls=model_calls,
                tool_calls=tool_calls,
            )

    @staticmethod
    def _validate_response_binding(bundle: PromptBundle, ref: object) -> None:
        request = bundle.model_request
        if (
            getattr(ref, "scope_digest", None) != scope_digest(request.run_scope)
            or getattr(ref, "attempt_id", None) != request.attempt_id
            or getattr(ref, "sequence", None) != request.sequence
        ):
            raise ToolContractError("model_response_binding_mismatch")

    @staticmethod
    def _completed(
        request: BirdARunRequest,
        bundle: PromptBundle,
        final_output: FinalOutput,
        *,
        model_calls: int,
        tool_calls: int,
    ) -> BirdARunOutcome:
        return BirdARunOutcome(
            status="completed",
            final_output=final_output,
            stop=None,
            model_calls=model_calls,
            tool_calls=tool_calls,
            prompt_policy_hash=bundle.prompt_policy_hash,
            rendered_prompt_hash=bundle.rendered_prompt_hash,
            tool_hash=bundle.tool_hash,
            context_hash=bundle.context_hash,
            config_hash=bundle.config_hash,
            attempt_id=request.attempt_id,
        )

    @staticmethod
    def _stopped(
        request: BirdARunRequest,
        bundle: PromptBundle,
        stop: StopOutcome,
        *,
        model_calls: int,
        tool_calls: int,
    ) -> BirdARunOutcome:
        return BirdARunOutcome(
            status="stopped",
            final_output=None,
            stop=stop,
            model_calls=model_calls,
            tool_calls=tool_calls,
            prompt_policy_hash=bundle.prompt_policy_hash,
            rendered_prompt_hash=bundle.rendered_prompt_hash,
            tool_hash=bundle.tool_hash,
            context_hash=bundle.context_hash,
            config_hash=bundle.config_hash,
            attempt_id=request.attempt_id,
        )


def _evidence_digest(
    calls: ToolCallOutput,
    results: tuple[ToolResult, ...],
) -> str:
    return sha256(
        _canonical_json(
            [
                {
                    "arguments": json.loads(call.arguments_json),
                    "name": call.name,
                    "result_sha256": result.content_sha256,
                }
                for call, result in zip(calls.tool_calls, results, strict=True)
            ]
        ).encode("utf-8")
    ).hexdigest()
