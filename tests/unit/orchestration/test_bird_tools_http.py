"""Contract tests for the production HTTP BirdToolPort (offline, scripted transport).

Covers the v0.3 §14.2 transport semantics: stateless reads retry 429/5xx with
bounded backoff (Retry-After wins), stateful actions never retry (send-time
interruption is `ambiguous_delivery`, a received rejection is a definitive
error result), and only frozen-contract actions with typed arguments dispatch.
"""

import json
from collections.abc import Mapping, Sequence

import pytest

from commerce_agent.model.contracts import ToolCall
from commerce_agent.orchestration.bird_tools_http import (
    BirdHttpResponse,
    BirdToolEndpoint,
    HttpBirdToolPort,
)
from commerce_agent.orchestration.tools import ToolContractError, ToolInfrastructureError

DB_ENV_BASE = "http://127.0.0.1:6002"
USER_SIM_BASE = "http://127.0.0.1:6001"
TASK_ID = "task-1"


class FakeTransport:
    def __init__(self, script: Sequence[BirdHttpResponse | Exception]) -> None:
        self._script = list(script)
        self.requests: list[dict[str, object]] = []

    async def post_json(
        self, url: str, payload: Mapping[str, object], timeout_seconds: float
    ) -> BirdHttpResponse:
        self.requests.append(
            {"url": url, "payload": dict(payload), "timeout": timeout_seconds}
        )
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class RecordingSleeper:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def response(
    status_code: int,
    body: object = None,
    headers: Mapping[str, str] | None = None,
) -> BirdHttpResponse:
    encoded = json.dumps(body).encode("utf-8") if body is not None else b""
    return BirdHttpResponse(
        status_code=status_code, headers=dict(headers or {}), body=encoded
    )


def make_port(
    transport: FakeTransport, sleeper: RecordingSleeper | None = None
) -> HttpBirdToolPort:
    return HttpBirdToolPort(
        db_env=BirdToolEndpoint(base_url=DB_ENV_BASE),
        user_sim=BirdToolEndpoint(base_url=USER_SIM_BASE),
        task_id=TASK_ID,
        transport=transport,
        sleeper=sleeper or RecordingSleeper(),
        jitter=lambda: 0.0,
    )


def call(name: str, arguments: dict[str, object], call_id: str = "call_1") -> ToolCall:
    return ToolCall(
        call_id=call_id,
        name=name,
        arguments_json=json.dumps(arguments, sort_keys=True, separators=(",", ":")),
    )


@pytest.mark.asyncio
async def test_non_allowlisted_action_is_rejected() -> None:
    transport = FakeTransport([])
    port = make_port(transport)

    with pytest.raises(ToolContractError) as excinfo:
        await port.execute(call("drop_table", {}))

    assert excinfo.value.reason_code == "action_not_allowlisted"
    assert transport.requests == []


@pytest.mark.asyncio
async def test_stateless_read_retries_then_succeeds() -> None:
    transport = FakeTransport([response(503), response(200, {"schema": "s"})])
    port = make_port(transport)

    result = await port.execute(call("get_schema", {}))

    assert result.status == "success"
    assert result.content_json == '{"schema":"s"}'
    assert len(transport.requests) == 2
    assert transport.requests[0]["url"] == f"{DB_ENV_BASE}/schema"
    assert transport.requests[0]["payload"] == {"task_id": TASK_ID}
    assert transport.requests[0]["timeout"] == 30.0


@pytest.mark.asyncio
async def test_stateless_read_exhausts_retries() -> None:
    transport = FakeTransport([response(503), response(503), response(503)])
    sleeper = RecordingSleeper()
    port = make_port(transport, sleeper)

    with pytest.raises(ToolInfrastructureError) as excinfo:
        await port.execute(call("get_schema", {}))

    assert excinfo.value.reason_code == "stateless_transport_exhausted"
    assert len(transport.requests) == 3
    assert sleeper.sleeps == [0.5, 1.0]


@pytest.mark.asyncio
async def test_retry_after_header_wins_over_backoff() -> None:
    transport = FakeTransport(
        [response(429, None, {"Retry-After": "7"}), response(200, {"schema": "s"})]
    )
    sleeper = RecordingSleeper()
    port = make_port(transport, sleeper)

    result = await port.execute(call("get_schema", {}))

    assert result.status == "success"
    assert sleeper.sleeps == [7.0]


@pytest.mark.asyncio
async def test_stateful_timeout_is_ambiguous_delivery() -> None:
    transport = FakeTransport([TimeoutError()])
    port = make_port(transport)

    with pytest.raises(ToolInfrastructureError) as excinfo:
        await port.execute(call("submit_sql", {"sql": "SELECT 1"}))

    assert excinfo.value.reason_code == "ambiguous_delivery"
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_stateful_clean_rejection_is_definitive_error_result() -> None:
    transport = FakeTransport([response(503)])
    port = make_port(transport)

    result = await port.execute(call("submit_sql", {"sql": "SELECT 1"}))

    assert result.status == "error"
    assert result.error_class == "service_http_rejected"
    assert '"status_code":503' in result.content_json
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_execute_sql_happy_path() -> None:
    transport = FakeTransport([response(200, {"result": "r", "success": True})])
    port = make_port(transport)

    result = await port.execute(call("execute_sql", {"sql": "SELECT 1"}))

    assert result.status == "success"
    assert result.content_json == '{"result":"r","success":true}'
    assert transport.requests[0]["url"] == f"{DB_ENV_BASE}/execute"
    assert transport.requests[0]["payload"] == {"task_id": TASK_ID, "sql": "SELECT 1"}
    assert transport.requests[0]["timeout"] == 120.0


@pytest.mark.asyncio
async def test_ask_user_targets_user_sim_endpoint() -> None:
    transport = FakeTransport([response(200, {"answer": "a"})])
    port = make_port(transport)

    result = await port.execute(call("ask_user", {"question": "which year?"}))

    assert result.status == "success"
    assert transport.requests[0]["url"] == f"{USER_SIM_BASE}/ask"
    assert transport.requests[0]["payload"] == {"task_id": TASK_ID, "question": "which year?"}
    assert transport.requests[0]["timeout"] == 60.0


@pytest.mark.asyncio
async def test_submit_sql_triggers_phase_transition_on_passed_p1_with_follow_up() -> None:
    submit_body = {
        "passed": True,
        "message": "ok",
        "reward": 1.0,
        "phase_completed": 1,
        "has_follow_up": True,
        "follow_up_query": "q",
    }
    transport = FakeTransport([response(200, submit_body), response(200, {})])
    port = make_port(transport)

    result = await port.execute(call("submit_sql", {"sql": "SELECT 1"}))

    assert result.status == "success"
    assert result.content_json == json.dumps(submit_body, sort_keys=True, separators=(",", ":"))
    assert len(transport.requests) == 2
    assert transport.requests[1]["url"] == f"{USER_SIM_BASE}/phase_transition"
    assert transport.requests[1]["payload"] == {"task_id": TASK_ID}


@pytest.mark.asyncio
async def test_submit_sql_skips_phase_transition_without_follow_up() -> None:
    submit_body = {
        "passed": True,
        "message": "ok",
        "reward": 1.0,
        "phase_completed": 1,
        "has_follow_up": False,
        "follow_up_query": None,
    }
    transport = FakeTransport([response(200, submit_body)])
    port = make_port(transport)

    await port.execute(call("submit_sql", {"sql": "SELECT 1"}))

    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_extra_argument_fields_are_rejected() -> None:
    transport = FakeTransport([])
    port = make_port(transport)

    with pytest.raises(ToolContractError) as excinfo:
        await port.execute(call("execute_sql", {"sql": "SELECT 1", "evil": True}))

    assert excinfo.value.reason_code == "invalid_action_arguments"
    assert transport.requests == []


@pytest.mark.asyncio
async def test_malformed_stateful_response_is_ambiguous_delivery() -> None:
    transport = FakeTransport([response(200, None)])
    port = make_port(transport)

    with pytest.raises(ToolInfrastructureError) as excinfo:
        await port.execute(call("submit_sql", {"sql": "SELECT 1"}))

    assert excinfo.value.reason_code == "ambiguous_delivery"
