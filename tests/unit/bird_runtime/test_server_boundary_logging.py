"""Server boundary logging: every mapped 400/500 must leave a sanitized log.

Task 13 pre-run regression (2026-09-13): three live failures produced bare
400/500 responses with no diagnostic trace in container logs, turning each
next failure into a guessing game. Logging is exception-type/loc only —
payload values never enter the log (HANDOFF pitfall 4), responses stay
sanitized reason codes.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from bird_system_agent.server import BirdSystemAgentApp


class _BrokenModel(BaseModel, extra="forbid"):
    known: str


def _validation_error() -> ValidationError:
    try:
        _BrokenModel.model_validate({"known": "x", "unexpected": "sensitive-input-value"})
    except ValidationError as error:
        return error
    raise AssertionError("unreachable")


class _RaisingAdapter:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def init_session(self, request: object) -> object:
        raise self._error


def _call_app(adapter: object) -> tuple[int, dict[str, Any]]:
    import asyncio

    app = BirdSystemAgentApp(adapter)  # type: ignore[arg-type]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/init_session",
        "headers": [(b"content-length", b"2")],
    }
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {
            "type": "http.request",
            "body": b'{"task_id": "t", "mode": "a-interact", "state": {}, "reset": false}',
            "more_body": False,
        }

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    asyncio.run(app(scope, receive, send))  # type: ignore[arg-type]
    status = messages[0]["status"]
    body = json.loads(messages[1]["body"])
    return status, body


def test_validation_error_logs_loc_and_type_but_not_values(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="bird_system_agent"):
        status, body = _call_app(_RaisingAdapter(_validation_error()))
    assert status == 400
    assert body == {"error": {"reason_code": "invalid_request"}}
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "ValidationError" in joined
    assert "unexpected" in joined  # field name (loc) is diagnostic metadata
    assert "sensitive-input-value" not in joined  # input values never reach the log


def test_value_error_logs_type_and_message(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="bird_system_agent"):
        status, body = _call_app(_RaisingAdapter(ValueError("tokenizer data file mismatch")))
    assert status == 400
    assert body == {"error": {"reason_code": "invalid_request"}}
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "ValueError" in joined
    assert "tokenizer data file mismatch" in joined


def test_unknown_exception_logs_traceback_and_returns_500(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("ERROR", logger="bird_system_agent"):
        status, body = _call_app(_RaisingAdapter(RuntimeError("boom")))
    assert status == 500
    assert body == {"error": {"reason_code": "internal_error"}}
    assert "RuntimeError" in caplog.text  # traceback (type line) is in the formatted log
