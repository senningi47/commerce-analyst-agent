"""Contract tests for the pure-ASGI system-agent server shell.

Verifies the three frozen endpoints, sanitized error mapping, and ASGI
lifespan handling with an in-memory client — no network, no real factory.
"""

import asyncio
import json
from typing import Any
from uuid import uuid4

from bird_system_agent.server import BirdSystemAgentApp, create_app
from commerce_agent.model.contracts import (
    CostUnavailable,
    RunScope,
    ToolResult,
    UsageUnavailable,
)
from commerce_agent.orchestration.bird_server import BirdCResponse
from commerce_agent.orchestration.contracts import AskUserCandidate
from commerce_agent.orchestration.tools import ToolContractError, ToolInfrastructureError

# --- light adapter harness (no ProfileRegistry dependency) -----------------


class LightFactory:
    def build_run_scope(self, *, mode: str, task_id: str) -> RunScope:
        return RunScope(
            run_id=uuid4(),
            track="bird",
            mode=mode,
            subject_id=task_id,
            experiment_id="day5",
            config_hash="a" * 64,
        )


class RaisingPort:
    async def execute(self, call: object) -> ToolResult:
        raise ToolContractError("action_not_allowlisted")


def _c_response(question: str) -> BirdCResponse:
    return BirdCResponse(
        candidate=AskUserCandidate(type="ask_user", question=question),
        usage=UsageUnavailable(status="unavailable", reason_code="stub"),
        cost=CostUnavailable(status="unavailable", reason_code="stub"),
        prompt_policy_hash="a" * 64,
        rendered_prompt_hash="b" * 64,
        tool_hash="c" * 64,
        context_hash="d" * 64,
        config_hash="e" * 64,
        attempt_id=uuid4(),
    )


class EndlessAskHandler:
    async def respond(self, request: object) -> BirdCResponse:
        del request
        return _c_response("more?")


class RaisingCHandler:
    async def respond(self, request: object) -> BirdCResponse:
        del request
        raise ToolContractError("bird_c_candidate_required")


class RaisingAPort(RaisingPort):
    pass


def _server_adapter(handler: object, port: object) -> Any:
    from commerce_agent.orchestration.bird_server import BirdSystemServerAdapter

    factory = LightFactory()
    factory.build_tool_port = lambda *, task_id: port  # type: ignore[method-assign]
    factory.build_c = lambda *, run_scope: handler  # type: ignore[method-assign]
    return BirdSystemServerAdapter(factory=factory)


class ASGIClient:
    def __init__(self, app: BirdSystemAgentApp) -> None:
        self._app = app

    async def request(
        self, method: str, path: str, body: dict[str, Any] | str | None = None
    ) -> tuple[int, Any]:
        raw = (
            body.encode("utf-8")
            if isinstance(body, str)
            else json.dumps(body).encode("utf-8")
            if body is not None
            else b""
        )
        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"",
        }
        messages: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": raw, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        await self._app(scope, receive, send)
        status = messages[0]["status"]
        payload = b"".join(m.get("body", b"") for m in messages[1:])
        return status, json.loads(payload.decode("utf-8")) if payload else None


# --- tests -----------------------------------------------------------------


class CannedAnswerPort:
    async def execute(self, call: object) -> ToolResult:
        del call
        content = json.dumps({"answer": "x"}, sort_keys=True, separators=(",", ":"))
        digest = "0" * 64
        return ToolResult(
            call_id="canned",
            name="ask_user",
            status="success",
            content_json=content,
            deterministic_summary=content,
            content_sha256=digest.replace("0", "a"),
            source_refs=("canned",),
        )


def _client(handler: object = None, port: object = RaisingPort()) -> ASGIClient:
    adapter = _server_adapter(handler or EndlessAskHandler(), port)
    return ASGIClient(create_app(adapter))


def test_healthz_reports_service_identity() -> None:
    status, payload = asyncio.run(_client().request("GET", "/healthz"))

    assert status == 200
    assert payload == {
        "status": "healthy",
        "service": "bird_system_agent",
        "adk_available": True,
    }


def test_init_session_round_trips_contract_fields() -> None:
    status, payload = asyncio.run(
        _client().request(
            "POST",
            "/init_session",
            {"task_id": "t1", "mode": "c-interact", "state": {}, "reset": True},
        )
    )

    assert status == 200
    assert set(payload) == {"task_id", "mode", "session_id", "adk_available"}
    assert payload["task_id"] == "t1"
    assert payload["adk_available"] is True


def test_run_session_returns_official_state_shape() -> None:
    status, payload = asyncio.run(
        _client(port=CannedAnswerPort()).request(
            "POST",
            "/run_session",
            {"task_id": "t1", "mode": "c-interact", "message": "go"},
        )
    )

    assert status == 200
    assert set(payload) == {
        "task_id",
        "mode",
        "session_id",
        "response",
        "state",
        "adk_available",
    }
    assert payload["response"] == "Maximum turns reached. Task ended."
    assert payload["state"]["model_turns"] == 60


def test_invalid_body_maps_to_sanitized_400() -> None:
    status, payload = asyncio.run(
        _client().request("POST", "/init_session", '{"task_id": "t1", "bogus": true}')
    )

    assert status == 400
    assert payload == {"error": {"reason_code": "invalid_request"}}


def test_unknown_path_maps_to_404() -> None:
    status, payload = asyncio.run(_client().request("GET", "/nope"))

    assert status == 404
    assert payload == {"error": {"reason_code": "not_found"}}


def test_wrong_method_maps_to_405() -> None:
    status, payload = asyncio.run(_client().request("GET", "/init_session"))

    assert status == 405
    assert payload == {"error": {"reason_code": "method_not_allowed"}}


def test_contract_error_maps_to_400_with_reason_code() -> None:
    client = _client(handler=RaisingCHandler())

    status, payload = asyncio.run(
        client.request(
            "POST",
            "/run_session",
            {"task_id": "t1", "mode": "c-interact", "message": "go"},
        )
    )

    assert status == 400
    assert payload == {"error": {"reason_code": "bird_c_candidate_required"}}


def test_infrastructure_error_maps_to_503() -> None:
    class InfraPort:
        async def execute(self, call: object) -> ToolResult:
            raise ToolInfrastructureError("ambiguous_delivery")

    client = _client(port=InfraPort())

    status, payload = asyncio.run(
        client.request(
            "POST",
            "/run_session",
            {"task_id": "t1", "mode": "c-interact", "message": "go"},
        )
    )

    assert status == 503
    assert payload == {"error": {"reason_code": "ambiguous_delivery"}}


def test_empty_body_is_invalid_request() -> None:
    status, payload = asyncio.run(_client().request("POST", "/init_session"))

    assert status == 400
    assert payload == {"error": {"reason_code": "invalid_request"}}


def test_lifespan_startup_and_shutdown_complete() -> None:
    app = create_app(_server_adapter(EndlessAskHandler(), RaisingPort()))
    messages: list[dict[str, Any]] = []
    calls = {"count": 0}

    async def receive() -> dict[str, Any]:
        calls["count"] += 1
        if calls["count"] == 1:
            return {"type": "lifespan.startup"}
        return {"type": "lifespan.shutdown"}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    asyncio.run(app({"type": "lifespan"}, receive, send))

    assert [m["type"] for m in messages] == [
        "lifespan.startup.complete",
        "lifespan.shutdown.complete",
    ]
