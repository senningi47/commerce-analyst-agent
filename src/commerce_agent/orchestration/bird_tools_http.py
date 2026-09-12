"""Production HTTP BirdToolPort bound to the frozen official contract.

The port is the only place where BirdA tool actions become outbound HTTP:

- the action allowlist and each action's endpoint, service, timeout, and
  payload shape come exclusively from `load_official_contract()`;
- stateless reads may retry 429/5xx responses with bounded backoff
  (`Retry-After` wins, exponential backoff with jitter otherwise);
- stateful actions never retry: a timeout or connection interruption after
  send is `ambiguous_delivery`, and a received rejection is a definitive
  error result (v0.3 §14.2);
- callers cannot supply arbitrary URLs, methods, or bodies; the task binding
  is fixed at construction so each session gets its own port instance.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable, Mapping
from hashlib import sha256
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from commerce_agent.evaluation.contracts import BirdEndpointContract, load_official_contract
from commerce_agent.model.contracts import ToolCall, ToolResult
from commerce_agent.orchestration.tools import ToolContractError, ToolInfrastructureError

_MAX_STATELESS_ATTEMPTS = 3  # total attempts per stateless read (v0.3 §14.2 bounded retry)
_BACKOFF_BASE_SECONDS = 0.5
_STATEFUL_ACTIONS = frozenset({"execute_sql", "ask_user", "submit_sql"})


class BirdToolEndpoint(BaseModel):
    """Base URL of one official service; paths are appended from the frozen contract."""

    model_config = ConfigDict(frozen=True)

    base_url: str = Field(
        min_length=1,
        max_length=512,
        pattern=r"^https?://[A-Za-z0-9._:\-]+(?::[0-9]{1,5})?$",
    )


class BirdHttpResponse(BaseModel):
    """One received HTTP response; body stays raw until the port parses it."""

    model_config = ConfigDict(frozen=True)

    status_code: int
    headers: Mapping[str, str]
    body: bytes


class BirdHttpTransport(Protocol):
    """Outbound HTTP seam; production uses httpx, tests use scripted fakes."""

    async def post_json(
        self, url: str, payload: Mapping[str, object], timeout_seconds: float
    ) -> BirdHttpResponse: ...


class AsyncSleeper(Protocol):
    async def sleep(self, seconds: float) -> None: ...


class AsyncioSleeper:
    """Production sleeper backed by `asyncio.sleep`."""

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class _SqlArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1, max_length=100_000)


class _ColumnMeaningArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table_name: str = Field(min_length=1, max_length=256)
    column_name: str = Field(min_length=1, max_length=256)


class _KnowledgeNameArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_name: str = Field(min_length=1, max_length=256)


class _QuestionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4_000)


_TYPED_ARGUMENTS: dict[str, type[BaseModel]] = {
    "execute_sql": _SqlArguments,
    "get_column_meaning": _ColumnMeaningArguments,
    "get_knowledge_definition": _KnowledgeNameArguments,
    "ask_user": _QuestionArguments,
    "submit_sql": _SqlArguments,
}


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


class HttpBirdToolPort:
    """Dispatch frozen-contract BirdA actions over HTTP with §14.2 semantics."""

    def __init__(
        self,
        *,
        db_env: BirdToolEndpoint,
        user_sim: BirdToolEndpoint,
        task_id: str,
        transport: BirdHttpTransport,
        sleeper: AsyncSleeper | None = None,
        jitter: Callable[[], float] | None = None,
    ) -> None:
        contract = load_official_contract()
        self._service_base = {"db_env": db_env, "user_sim": user_sim}
        self._task_id = task_id
        self._transport = transport
        self._sleeper: AsyncSleeper = sleeper or AsyncioSleeper()
        self._jitter = jitter or random.random
        self._primary: dict[str, BirdEndpointContract] = {}
        self._phase_transition: BirdEndpointContract | None = None
        for entry in contract.outbound_endpoints:
            if entry.action == "submit_sql" and entry.path == "/phase_transition":
                self._phase_transition = entry
                continue
            self._primary.setdefault(entry.action, entry)

    async def execute(self, call: ToolCall) -> ToolResult:
        endpoint = self._primary.get(call.name)
        if endpoint is None:
            raise ToolContractError("action_not_allowlisted")
        payload = self._payload(endpoint, call)
        result = await self._request(call, endpoint, payload)
        if call.name == "submit_sql":
            result = await self._maybe_phase_transition(result)
        return result

    def _payload(self, endpoint: BirdEndpointContract, call: ToolCall) -> dict[str, object]:
        try:
            arguments = json.loads(call.arguments_json)
        except json.JSONDecodeError as error:
            raise ToolContractError("invalid_action_arguments") from error
        if not isinstance(arguments, dict):
            raise ToolContractError("invalid_action_arguments")
        argument_model = _TYPED_ARGUMENTS.get(call.name)
        if argument_model is None:
            if arguments:
                raise ToolContractError("invalid_action_arguments")
            fields: dict[str, object] = {}
        else:
            try:
                parsed = argument_model.model_validate(arguments)
            except ValidationError as error:
                raise ToolContractError("invalid_action_arguments") from error
            fields = dict(parsed.model_dump())
        return {"task_id": self._task_id, **fields}

    async def _request(
        self,
        call: ToolCall,
        endpoint: BirdEndpointContract,
        payload: dict[str, object],
    ) -> ToolResult:
        is_stateful = endpoint.action in _STATEFUL_ACTIONS
        url = self._url(endpoint)
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await self._transport.post_json(
                    url, payload, endpoint.timeout_seconds
                )
            except Exception as error:
                if not is_stateful and attempt < _MAX_STATELESS_ATTEMPTS:
                    await self._sleeper.sleep(self._backoff_seconds(attempt))
                    continue
                raise ToolInfrastructureError(
                    "ambiguous_delivery" if is_stateful else "stateless_transport_exhausted"
                ) from error
            if 200 <= response.status_code < 300:
                try:
                    body = json.loads(response.body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    if is_stateful:
                        # a received-but-unparseable reply leaves the outcome
                        # unknown, which is the §14.2 ambiguous delivery case
                        raise ToolInfrastructureError("ambiguous_delivery") from error
                    if attempt < _MAX_STATELESS_ATTEMPTS:
                        await self._sleeper.sleep(self._backoff_seconds(attempt))
                        continue
                    raise ToolInfrastructureError("stateless_transport_exhausted") from error
                return self._result(call, endpoint, body)
            retryable = response.status_code == 429 or response.status_code >= 500
            if is_stateful:
                # a received rejection is definitive: never re-send a stateful action
                return self._rejected_result(call, endpoint, response)
            if retryable and attempt < _MAX_STATELESS_ATTEMPTS:
                await self._sleeper.sleep(self._retry_delay(response, attempt))
                continue
            if retryable:
                raise ToolInfrastructureError("stateless_transport_exhausted")
            raise ToolInfrastructureError("service_http_rejected")

    async def _maybe_phase_transition(self, result: ToolResult) -> ToolResult:
        """Replicate the official submit_sql side call: passed P1 + follow-up
        triggers one POST to the user simulator; its failure is non-fatal."""

        if result.status != "success" or self._phase_transition is None:
            return result
        body = json.loads(result.content_json)
        if not (
            body.get("passed") is True
            and body.get("phase_completed") == 1
            and body.get("has_follow_up") is True
        ):
            return result
        transition = self._phase_transition
        try:
            await self._transport.post_json(
                self._url(transition),
                {"task_id": self._task_id},
                transition.timeout_seconds,
            )
        except Exception:  # noqa: BLE001, S110 -- official tools.py treats transition
            # failure as non-fatal (try/except with warning); any transport-layer
            # error type may surface here, so the broad catch is the contract.
            pass
        return result

    def _url(self, endpoint: BirdEndpointContract) -> str:
        base = self._service_base[endpoint.service].base_url
        return base.rstrip("/") + endpoint.path

    def _result(
        self, call: ToolCall, endpoint: BirdEndpointContract, body: object
    ) -> ToolResult:
        content_json = _canonical_json(body)
        content_sha256 = sha256(content_json.encode("utf-8")).hexdigest()
        source_refs = (f"bird:{endpoint.service}{endpoint.path}",)
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

    def _rejected_result(
        self, call: ToolCall, endpoint: BirdEndpointContract, response: BirdHttpResponse
    ) -> ToolResult:
        content_json = _canonical_json(
            {"path": endpoint.path, "status_code": response.status_code}
        )
        content_sha256 = sha256(content_json.encode("utf-8")).hexdigest()
        source_refs = (f"bird:{endpoint.service}{endpoint.path}",)
        return ToolResult(
            call_id=call.call_id,
            name=call.name,
            status="error",
            error_class="service_http_rejected",
            content_json=content_json,
            deterministic_summary=_canonical_json(
                {"content_sha256": content_sha256, "source_refs": source_refs}
            ),
            content_sha256=content_sha256,
            source_refs=source_refs,
        )

    def _retry_delay(self, response: BirdHttpResponse, attempt: int) -> float:
        retry_after = _header_value(response.headers, "Retry-After")
        if retry_after is not None:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        return self._backoff_seconds(attempt)

    def _backoff_seconds(self, attempt: int) -> float:
        return _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) * (1.0 + self._jitter())
