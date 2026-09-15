"""Single-exchange responder for the synthetic BirdC track."""

import json

from pydantic import ValidationError

from commerce_agent.context_builder.builder import ContextBuilder, scope_digest
from commerce_agent.context_builder.contracts import BirdCProfile, ContextRequest, PromptBundle
from commerce_agent.model._turn_store import AttemptRef, ProviderTurnStore
from commerce_agent.model.contracts import (
    FinalOutput,
    FinishReason,
    ModelGateway,
    ToolCall,
    ToolCallOutput,
)
from commerce_agent.orchestration.contracts import (
    AskUserCandidate,
    BirdCCandidate,
    BirdCRequest,
    BirdCResponse,
    SubmitSqlCandidate,
    TextCandidate,
)
from commerce_agent.orchestration.tools import ToolContractError, ToolInfrastructureError

# Responder-side allowlist; tests/contract/test_bird_tool_catalog.py pins it to
# the bird_c_respond inference rule.
BIRD_C_TOOL_NAMES = frozenset({"ask_user", "submit_sql"})


class BirdCResponder:
    """Build and execute exactly one model exchange for a BirdC phase."""

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        profile: BirdCProfile,
        gateway: ModelGateway,
        turn_store: ProviderTurnStore,
    ) -> None:
        if (profile.kind, profile.key) != ("bird_c", "bird_c"):
            raise ToolContractError("bird_c_profile_required")
        self._context_builder = context_builder
        self._profile = profile
        self._gateway = gateway
        self._turn_store = turn_store

    async def respond(self, request: BirdCRequest) -> BirdCResponse:
        """Return one candidate and destroy attempt-private state on exit."""

        try:
            return await self._respond_once(request)
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

    async def _respond_once(self, request: BirdCRequest) -> BirdCResponse:
        """Build and execute the phase's only provider exchange."""

        bundle = self._context_builder.build(
            ContextRequest(
                run_scope=request.run_scope,
                attempt_id=request.attempt_id,
                sequence=0,
                profile=self._profile,
                step="bird_c_respond",
                current_input=request.current_phase,
                confirmed_facts=request.confirmed_facts,
                evidence=request.evidence,
                latest_error=request.latest_error,
                history=(),
            )
        )
        model_response = await self._gateway.complete(bundle.model_request)
        self._validate_response_binding(bundle, model_response.provider_turn_ref)
        if model_response.finish_reason is not FinishReason.TOOL_CALLS or not isinstance(
            model_response.output, ToolCallOutput
        ):
            output = model_response.output
            if not isinstance(output, FinalOutput):
                raise ToolContractError("bird_c_candidate_required")
            # official ADK semantics: a non-function-call response ends the
            # runner invocation; the orchestrator's next phase message
            # continues with the text retained in session memory
            return BirdCResponse(
                candidate=TextCandidate(type="text", content=output.content),
                usage=model_response.usage,
                cost=model_response.cost,
                prompt_policy_hash=bundle.prompt_policy_hash,
                rendered_prompt_hash=bundle.rendered_prompt_hash,
                tool_hash=bundle.tool_hash,
                context_hash=bundle.context_hash,
                config_hash=bundle.config_hash,
                attempt_id=request.attempt_id,
            )
        if len(model_response.output.tool_calls) != 1:
            raise ToolContractError("bird_c_single_candidate_required")
        candidate = self._candidate(model_response.output.tool_calls[0])
        return BirdCResponse(
            candidate=candidate,
            usage=model_response.usage,
            cost=model_response.cost,
            prompt_policy_hash=bundle.prompt_policy_hash,
            rendered_prompt_hash=bundle.rendered_prompt_hash,
            tool_hash=bundle.tool_hash,
            context_hash=bundle.context_hash,
            config_hash=bundle.config_hash,
            attempt_id=request.attempt_id,
        )

    @staticmethod
    def _candidate(call: ToolCall) -> BirdCCandidate:
        name = call.name
        if name not in BIRD_C_TOOL_NAMES:
            raise ToolContractError("tool_not_registered")
        try:
            arguments = json.loads(call.arguments_json)
            if not isinstance(arguments, dict):
                raise TypeError
            if name == "ask_user":
                return AskUserCandidate.model_validate({"type": name, **arguments})
            return SubmitSqlCandidate.model_validate({"type": name, **arguments})
        except (TypeError, ValueError, ValidationError) as error:
            raise ToolContractError("invalid_tool_arguments") from error

    @staticmethod
    def _validate_response_binding(bundle: PromptBundle, ref: object) -> None:
        model_request = bundle.model_request
        if (
            getattr(ref, "scope_digest", None) != scope_digest(model_request.run_scope)
            or getattr(ref, "attempt_id", None) != model_request.attempt_id
            or getattr(ref, "sequence", None) != model_request.sequence
        ):
            raise ToolContractError("model_response_binding_mismatch")
