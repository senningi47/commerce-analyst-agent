"""Non-streaming DeepSeek HTTP adapter behind the provider-neutral gateway seam."""

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field, model_validator

from commerce_agent.context_builder._canonical import canonical_json, sha256_canonical
from commerce_agent.context_builder.builder import scope_digest
from commerce_agent.model._pricing import CostCalculator, PriceSnapshot
from commerce_agent.model._retry import RetryPolicy
from commerce_agent.model._turn_store import PrivateProviderTurn, ProviderTurnStore
from commerce_agent.model.contracts import (
    Cost,
    CostUnavailable,
    FinalOutput,
    FinishReason,
    ModelAttemptSummary,
    ModelRequest,
    ModelResponse,
    NonThinkingConfig,
    ReportedUsage,
    ThinkingConfig,
    ToolCall,
    ToolCallOutput,
    Usage,
    UsageUnavailable,
)
from commerce_agent.model.errors import (
    ModelAuthError,
    ModelBalanceError,
    ModelCapabilityError,
    ModelProtocolError,
    ModelRateLimited,
    ModelTransportError,
)

LOGGER = logging.getLogger(__name__)


def _json_or_none(response: httpx.Response) -> object:
    """Best-effort re-parse for diagnostics; returns None when the body itself
    is not JSON (structure unknown)."""
    try:
        return response.json()
    except Exception:  # noqa: BLE001 - diagnostic best effort, never fatal
        return None


class CapabilitySnapshot(BaseModel, frozen=True, extra="forbid"):
    revision: str = Field(min_length=1, max_length=128)
    requested_model: str = Field(min_length=1, max_length=256)
    response_model: str = Field(min_length=1, max_length=256)
    backend_model: str = Field(min_length=1, max_length=256)
    thinking_efforts: tuple[Literal["low", "high", "max"], ...]
    thinking_effort_field: Literal["reasoning_effort"]
    provider_user_id_field: Literal["user_id"]
    tool_calling: bool
    thinking_tool_choice: bool
    replay_rule: Literal["all_assistant_turns_when_tools"]
    expected_usage_fields: tuple[str, ...]
    context_token_limit: int = Field(ge=1)
    output_token_limit: int = Field(ge=1)
    probed_at: datetime
    source_urls: tuple[str, ...]
    source_content_hashes: tuple[str, ...]

    @model_validator(mode="after")
    def validate_snapshot(self) -> "CapabilitySnapshot":
        if self.probed_at.tzinfo is None or self.probed_at.utcoffset() is None:
            raise ValueError("probed_at must be timezone-aware")
        if len(self.source_urls) != len(self.source_content_hashes):
            raise ValueError("source URLs and hashes must align")
        if any(len(value) != 64 for value in self.source_content_hashes):
            raise ValueError("source hashes must be SHA-256 values")
        return self


class Clock(Protocol):
    def now(self) -> datetime: ...


class AttemptGuard(Protocol):
    async def before_send(
        self, request: ModelRequest, attempt_number: int, sent_at: datetime
    ) -> None: ...

    async def after_attempt(self, request: ModelRequest, summary: ModelAttemptSummary) -> None: ...


class NoopAttemptGuard:
    async def before_send(
        self, request: ModelRequest, attempt_number: int, sent_at: datetime
    ) -> None:
        return None

    async def after_attempt(self, request: ModelRequest, summary: ModelAttemptSummary) -> None:
        return None


class DeepSeekModelGateway:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        turn_store: ProviderTurnStore,
        capability: CapabilitySnapshot,
        prices: PriceSnapshot,
        retry_policy: RetryPolicy,
        clock: Clock,
        sleeper: Callable[[Decimal], Awaitable[None]],
        jitter_rng: Callable[[], Decimal],
        attempt_guard: AttemptGuard | None = None,
    ) -> None:
        self._client = client
        self._turn_store = turn_store
        self._capability = capability
        self._costs = CostCalculator(prices)
        self._retry = retry_policy
        self._clock = clock
        self._sleeper = sleeper
        self._jitter_rng = jitter_rng
        self._guard = attempt_guard or NoopAttemptGuard()

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self._validate_request(request)
        wire_body = await self._encode_request(request)
        attempts: list[ModelAttemptSummary] = []
        for attempt_number in range(1, self._retry.maximum_attempts + 1):
            sent_at = self._clock.now()
            await self._guard.before_send(request, attempt_number, sent_at)
            try:
                response = await self._client.post("/chat/completions", json=wire_body)
            except httpx.TransportError as error:
                completed_at = self._clock.now()
                charge_ambiguous = not isinstance(error, httpx.ConnectError)
                summary = self._summary(
                    attempt_number,
                    sent_at,
                    completed_at,
                    "transport_error",
                    retryable=True,
                    charge_ambiguous=charge_ambiguous,
                )
                attempts.append(summary)
                await self._guard.after_attempt(request, summary)
                if await self._wait_to_retry(attempt_number, None):
                    continue
                raise ModelTransportError(
                    "transport_retry_exhausted",
                    f"model transport failed at attempt {attempt_number}",
                    retryable=True,
                    charge_ambiguous=charge_ambiguous,
                    attempts=tuple(attempts),
                ) from error

            completed_at = self._clock.now()
            if response.status_code >= 400:
                retryable = response.status_code in {429, 500, 502, 503, 504}
                summary = self._summary(
                    attempt_number,
                    sent_at,
                    completed_at,
                    "http_error",
                    retryable=retryable,
                    charge_ambiguous=False,
                    http_status=response.status_code,
                )
                attempts.append(summary)
                await self._guard.after_attempt(request, summary)
                if retryable and await self._wait_to_retry(
                    attempt_number, response.headers.get("Retry-After")
                ):
                    continue
                self._raise_http(response.status_code, attempt_number, tuple(attempts))

            try:
                payload = response.json()
                finish = self._finish_reason(payload)
                usage = self._usage(payload)
                cost = self._cost(usage, sent_at)
            except (ValueError, TypeError, KeyError) as error:
                self._log_protocol_diagnostic(
                    site="payload_parse", error=error, payload=_json_or_none(response)
                )
                summary = self._summary(
                    attempt_number,
                    sent_at,
                    completed_at,
                    "protocol_error",
                    retryable=False,
                    charge_ambiguous=True,
                )
                attempts.append(summary)
                await self._guard.after_attempt(request, summary)
                raise ModelProtocolError(
                    "provider_response_invalid",
                    f"model protocol failed at attempt {attempt_number}",
                    retryable=False,
                    charge_ambiguous=True,
                    attempts=tuple(attempts),
                ) from error

            if finish is FinishReason.INSUFFICIENT_SYSTEM_RESOURCE:
                summary = self._summary(
                    attempt_number,
                    sent_at,
                    completed_at,
                    "insufficient_system_resource",
                    retryable=True,
                    charge_ambiguous=True,
                    usage=usage,
                    cost=cost,
                )
                attempts.append(summary)
                await self._guard.after_attempt(request, summary)
                if await self._wait_to_retry(attempt_number, None):
                    continue
                raise ModelTransportError(
                    "insufficient_system_resource",
                    f"model infrastructure exhausted at attempt {attempt_number}",
                    retryable=True,
                    charge_ambiguous=True,
                    attempts=tuple(attempts),
                )

            try:
                output, private_turn = self._parse_output(payload, request, usage, sent_at, finish)
            except (ValueError, TypeError, KeyError) as error:
                self._log_protocol_diagnostic(site="output_parse", error=error, payload=payload)
                summary = self._summary(
                    attempt_number,
                    sent_at,
                    completed_at,
                    "protocol_error",
                    retryable=False,
                    charge_ambiguous=True,
                    usage=usage,
                    cost=cost,
                )
                attempts.append(summary)
                await self._guard.after_attempt(request, summary)
                raise ModelProtocolError(
                    "provider_response_invalid",
                    f"model protocol failed at attempt {attempt_number}",
                    retryable=False,
                    charge_ambiguous=True,
                    attempts=tuple(attempts),
                ) from error

            summary = self._summary(
                attempt_number,
                sent_at,
                completed_at,
                "success",
                retryable=False,
                charge_ambiguous=False,
                usage=usage,
                cost=cost,
            )
            attempts.append(summary)
            await self._guard.after_attempt(request, summary)
            ref = await self._turn_store.save(private_turn)
            return ModelResponse(
                output=output,
                provider_turn_ref=ref,
                finish_reason=finish,
                actual_model=payload["model"],
                system_fingerprint=payload.get("system_fingerprint"),
                usage=usage,
                cost=cost,
                attempts=tuple(attempts),
            )
        raise AssertionError("bounded retry loop terminated unexpectedly")

    def _validate_request(self, request: ModelRequest) -> None:
        if request.capability_revision != self._capability.revision:
            raise ModelCapabilityError(
                "capability_revision_mismatch",
                "model capability revision does not match",
                retryable=False,
            )
        if request.inference.max_output_tokens > self._capability.output_token_limit:
            raise ModelCapabilityError(
                "output_limit_unsupported",
                "model output limit is unsupported",
                retryable=False,
            )
        if request.tools and not self._capability.tool_calling:
            raise ModelCapabilityError(
                "tool_calling_unsupported",
                "model tool calling is unsupported",
                retryable=False,
            )
        if (
            isinstance(request.inference, ThinkingConfig)
            and request.inference.effort not in self._capability.thinking_efforts
        ):
            raise ModelCapabilityError(
                "thinking_effort_unsupported",
                "model thinking effort is unsupported",
                retryable=False,
            )
        digest = scope_digest(request.run_scope)
        sequences: list[int] = []
        for group in request.history:
            ref = group.provider_turn_ref
            if ref.scope_digest != digest or ref.attempt_id != request.attempt_id:
                raise ModelCapabilityError(
                    "history_binding_mismatch",
                    "model history binding does not match request",
                    retryable=False,
                )
            sequences.append(ref.sequence)
        if sequences != sorted(set(sequences)) or any(
            sequence >= request.sequence for sequence in sequences
        ):
            raise ModelCapabilityError(
                "history_sequence_invalid",
                "model history sequence is invalid",
                retryable=False,
            )

    async def _encode_request(self, request: ModelRequest) -> dict[str, object]:
        messages: list[dict[str, object]] = []
        current_messages = list(request.messages)
        trailing_user = current_messages.pop() if current_messages[-1].role == "user" else None
        messages.extend(message.model_dump() for message in current_messages)
        for group in request.history:
            private = await self._turn_store.resolve(group.provider_turn_ref)
            assistant: dict[str, object] = {"role": "assistant", "content": private.content}
            if private.reasoning_content is not None:
                assistant["reasoning_content"] = private.reasoning_content
            tool_calls = json.loads(private.tool_calls_json)
            if tool_calls:
                assistant["tool_calls"] = tool_calls
            messages.append(assistant)
            if hasattr(group, "tool_results"):
                for result in group.tool_results:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": result.call_id,
                            "name": result.name,
                            "content": result.content_json,
                        }
                    )
        if trailing_user is not None:
            messages.append(trailing_user.model_dump())
        body: dict[str, object] = {
            "model": self._capability.requested_model,
            "messages": messages,
            "max_tokens": request.inference.max_output_tokens,
            "stream": False,
            self._capability.provider_user_id_field: request.provider_user_id,
        }
        if isinstance(request.inference, ThinkingConfig):
            body["thinking"] = {"type": "enabled"}
            body[self._capability.thinking_effort_field] = request.inference.effort
        elif isinstance(request.inference, NonThinkingConfig):
            body["thinking"] = {"type": "disabled"}
            body["temperature"] = request.inference.temperature
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": json.loads(tool.parameters_json),
                    },
                }
                for tool in request.tools
            ]
        return body

    def _usage(self, payload: dict[str, object]) -> Usage:
        raw = payload.get("usage")
        if raw is None:
            return UsageUnavailable(status="unavailable", reason_code="provider_usage_missing")
        if not isinstance(raw, dict):
            raise TypeError("usage must be an object")
        details = raw.get("completion_tokens_details") or {}
        if not isinstance(details, dict):
            raise TypeError("completion token details must be an object")
        return ReportedUsage(
            status="reported",
            prompt_tokens=raw["prompt_tokens"],
            cache_hit_tokens=raw["prompt_cache_hit_tokens"],
            cache_miss_tokens=raw["prompt_cache_miss_tokens"],
            completion_tokens=raw["completion_tokens"],
            reasoning_tokens=details.get("reasoning_tokens", 0),
            total_tokens=raw["total_tokens"],
        )

    def _cost(self, usage: Usage, sent_at: datetime) -> Cost:
        if isinstance(usage, ReportedUsage):
            return self._costs.calculate(usage, sent_at)
        return CostUnavailable(status="unavailable", reason_code="usage_unavailable")

    @staticmethod
    def _finish_reason(payload: dict[str, object]) -> FinishReason:
        choices = payload["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("response must contain one choice")
        return FinishReason(choices[0]["finish_reason"])

    def _parse_output(
        self,
        payload: dict[str, object],
        request: ModelRequest,
        usage: Usage,
        sent_at: datetime,
        finish: FinishReason,
    ) -> tuple[FinalOutput | ToolCallOutput, PrivateProviderTurn]:
        if payload.get("model") != self._capability.response_model:
            raise ValueError("response model mismatch")
        choice = payload["choices"][0]
        message = choice["message"]
        if not isinstance(message, dict):
            raise TypeError("assistant message must be an object")
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise TypeError("tool calls must be a list")
        calls: list[ToolCall] = []
        for raw_call in raw_calls:
            function = raw_call["function"]
            arguments = json.loads(function["arguments"])
            calls.append(
                ToolCall(
                    call_id=raw_call["id"],
                    name=function["name"],
                    arguments_json=canonical_json(arguments),
                )
            )
        if calls and finish is not FinishReason.TOOL_CALLS:
            raise ValueError("tool calls require tool_calls finish reason")
        if not calls and finish is FinishReason.TOOL_CALLS:
            raise ValueError("tool_calls finish reason requires tool calls")
        if calls:
            output: FinalOutput | ToolCallOutput = ToolCallOutput(
                type="tool_calls", tool_calls=tuple(calls)
            )
        else:
            output = FinalOutput(type="final", content=message["content"])
        canonical_calls = canonical_json(raw_calls)
        private_payload = {
            "content": message.get("content"),
            "reasoning_content": message.get("reasoning_content"),
            "tool_calls": raw_calls,
        }
        token_weight = usage.completion_tokens if isinstance(usage, ReportedUsage) else 0
        turn = PrivateProviderTurn(
            scope_digest=scope_digest(request.run_scope),
            attempt_id=request.attempt_id,
            sequence=request.sequence,
            provider="deepseek",
            model=payload["model"],
            content=message.get("content"),
            reasoning_content=message.get("reasoning_content"),
            tool_calls_json=canonical_calls,
            payload_sha256=sha256_canonical(private_payload),
            expected_tool_call_ids=tuple(call.call_id for call in calls),
            token_weight=token_weight,
            created_at=sent_at,
            last_used_at=sent_at,
            absolute_expires_at=sent_at + timedelta(days=7),
        )
        return output, turn

    @staticmethod
    def _summary(
        attempt_number: int,
        sent_at: datetime,
        completed_at: datetime,
        outcome: Literal[
            "success",
            "http_error",
            "transport_error",
            "protocol_error",
            "insufficient_system_resource",
        ],
        *,
        retryable: bool,
        charge_ambiguous: bool,
        http_status: int | None = None,
        usage: Usage | None = None,
        cost: Cost | None = None,
    ) -> ModelAttemptSummary:
        return ModelAttemptSummary(
            attempt_number=attempt_number,
            sent_at=sent_at,
            completed_at=completed_at,
            outcome=outcome,
            http_status=http_status,
            retryable=retryable,
            charge_ambiguous=charge_ambiguous,
            usage=usage or UsageUnavailable(status="unavailable", reason_code="attempt_failed"),
            cost=cost or CostUnavailable(status="unavailable", reason_code="attempt_failed"),
        )

    def _log_protocol_diagnostic(
        self, *, site: str, error: Exception, payload: object
    ) -> None:
        """Sanitized protocol-failure metadata (pit 72): structure, exception
        classes and positions only — never response content. Agent-visible
        container log, not GT, so repeated live failures are diagnosable
        offline instead of burning paid diagnostic runs."""
        finish_raw: object = None
        model_echo: object = None
        payload_keys: object = None
        if isinstance(payload, dict):
            payload_keys = sorted(str(key) for key in payload)
            model_echo = payload.get("model")
            choices = payload.get("choices")
            if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict):
                finish_raw = choices[0].get("finish_reason")
        LOGGER.warning(
            "protocol_diagnostic site=%s error=%s detail=%s finish_reason=%r "
            "model_echo_match=%s model_echo=%r payload_keys=%s",
            site,
            type(error).__name__,
            error,
            finish_raw,
            model_echo == self._capability.response_model,
            model_echo,
            payload_keys,
        )

    async def _wait_to_retry(self, attempt_number: int, retry_after: str | None) -> bool:
        decision = self._retry.decision(
            attempt_number=attempt_number,
            retry_after=retry_after,
            jitter=self._jitter_rng(),
        )
        if not decision.should_retry:
            return False
        await self._sleeper(decision.delay_seconds)
        return True

    @staticmethod
    def _raise_http(
        status: int,
        attempt_number: int,
        attempts: tuple[ModelAttemptSummary, ...],
    ) -> None:
        kwargs = {
            "retryable": False,
            "charge_ambiguous": False,
            "attempts": attempts,
        }
        if status == 401:
            raise ModelAuthError(
                "provider_auth_rejected", f"model HTTP 401 at attempt {attempt_number}", **kwargs
            )
        if status == 402:
            raise ModelBalanceError(
                "provider_balance_insufficient",
                f"model HTTP 402 at attempt {attempt_number}",
                **kwargs,
            )
        if status == 429:
            raise ModelRateLimited(
                "provider_rate_limited",
                f"model HTTP 429 at attempt {attempt_number}",
                retryable=True,
                charge_ambiguous=False,
                attempts=attempts,
            )
        if status >= 500:
            raise ModelTransportError(
                "provider_http_retry_exhausted",
                f"model HTTP {status} at attempt {attempt_number}",
                retryable=True,
                charge_ambiguous=False,
                attempts=attempts,
            )
        raise ModelCapabilityError(
            "provider_http_rejected",
            f"model HTTP {status} at attempt {attempt_number}",
            **kwargs,
        )
