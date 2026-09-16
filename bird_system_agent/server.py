"""Pure-ASGI system-agent server implementing the frozen official contract.

Three endpoints only: `GET /healthz`, `POST /init_session`, `POST /run_session`
(field sets frozen by `load_official_contract()` and asserted in contract
tests). The app is a plain ASGI callable so the product lockfile stays
untouched; uvicorn (container-only dependency) serves it unchanged. Error
responses carry a sanitized reason code only — never provider or server
detail (HANDOFF pitfall 4). Boundary logs carry exception type/loc metadata
only — never payload values — so live failures stay diagnosable.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Protocol

from pydantic import ValidationError

from commerce_agent.model.errors import ModelGatewayError
from commerce_agent.orchestration.bird_server import (
    BirdInitSessionRequest,
    BirdRunSessionRequest,
    BirdSystemServerAdapter,
)
from commerce_agent.orchestration.tools import ToolContractError, ToolInfrastructureError

logger = logging.getLogger("bird_system_agent")

_MAX_BODY_BYTES = 1_048_576
_JSON_HEADERS = [(b"content-type", b"application/json")]


class ASGIReceive(Protocol):
    async def __call__(self) -> dict[str, Any]: ...


class ASGISend(Protocol):
    async def __call__(self, message: dict[str, Any]) -> None: ...


class _ShutdownHook(Protocol):
    async def __call__(self) -> None: ...


def _reason_code(error: Exception) -> str:
    code = getattr(error, "reason_code", None)
    return code if isinstance(code, str) and code else "internal_error"


class BirdSystemAgentApp:
    """ASGI application wrapping one configured `BirdSystemServerAdapter`."""

    def __init__(
        self, adapter: BirdSystemServerAdapter, *, on_shutdown: _ShutdownHook | None = None
    ) -> None:
        self._adapter = adapter
        self._on_shutdown = on_shutdown

    async def __call__(self, scope: dict[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        scope_type = scope["type"]
        if scope_type == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope_type != "http":
            return
        try:
            await self._dispatch(scope, receive, send)
        except ValidationError as error:
            logger.warning(
                "boundary 400 invalid_request ValidationError errors=%s",
                [
                    # loc + type + the validator's static message; payload
                    # values (item["input"]) are deliberately excluded
                    f"{'/'.join(str(loc) for loc in item.get('loc', ()))}:"
                    f"{item.get('type')}:{str(item.get('msg'))[:160]}"
                    for item in error.errors()
                ],
            )
            await _send_json(send, 400, {"error": {"reason_code": "invalid_request"}})
        except ValueError as error:
            # malformed JSON / oversized / empty body
            logger.warning("boundary 400 invalid_request ValueError msg=%.200s", error)
            await _send_json(send, 400, {"error": {"reason_code": "invalid_request"}})
        except ToolContractError as error:
            logger.warning("boundary 400 ToolContractError reason=%s", _reason_code(error))
            await _send_json(send, 400, {"error": {"reason_code": _reason_code(error)}})
        except (ToolInfrastructureError, ModelGatewayError) as error:
            logger.warning(
                "boundary 503 type=%s reason=%s",
                type(error).__name__,
                _reason_code(error),
            )
            await _send_json(send, 503, {"error": {"reason_code": _reason_code(error)}})
        except Exception:
            logger.exception("boundary 500 internal_error")
            await _send_json(send, 500, {"error": {"reason_code": "internal_error"}})

    async def _lifespan(self, receive: ASGIReceive, send: ASGISend) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                if self._on_shutdown is not None:
                    try:
                        await self._on_shutdown()
                    except Exception:  # noqa: BLE001, S110 -- shutdown must complete
                        pass
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _dispatch(self, scope: dict[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        method = scope["method"]
        path = scope["path"]
        if path == "/healthz" and method == "GET":
            await _send_json(
                send,
                200,
                {"status": "healthy", "service": "bird_system_agent", "adk_available": True},
            )
            return
        if path in {"/init_session", "/run_session"}:
            if method != "POST":
                await _send_json(send, 405, {"error": {"reason_code": "method_not_allowed"}})
                return
            body = await _read_json_body(receive)
            if path == "/init_session":
                response = self._adapter.init_session(BirdInitSessionRequest.model_validate(body))
            else:
                response = await self._adapter.run_session(
                    BirdRunSessionRequest.model_validate(body)
                )
            await _send_json(send, 200, response.model_dump(mode="json"))
            return
        await _send_json(send, 404, {"error": {"reason_code": "not_found"}})


async def _read_json_body(receive: ASGIReceive) -> Any:
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        body = message.get("body", b"")
        total += len(body)
        if total > _MAX_BODY_BYTES:
            raise ValueError("request body too large")
        chunks.append(body)
        if not message.get("more_body", False):
            break
    if not chunks:
        raise ValueError("empty JSON body")
    return json.loads(b"".join(chunks).decode("utf-8"))


async def _send_json(send: ASGISend, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [*_JSON_HEADERS, (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


def create_app(
    adapter: BirdSystemServerAdapter, *, on_shutdown: _ShutdownHook | None = None
) -> BirdSystemAgentApp:
    return BirdSystemAgentApp(adapter, on_shutdown=on_shutdown)


def main() -> None:  # pragma: no cover - container entry point
    import uvicorn

    from bird_system_agent.runtime import DeepSeekBirdRuntimeFactory
    from commerce_agent.orchestration.bird_server import BirdSystemServerAdapter

    factory = DeepSeekBirdRuntimeFactory.from_env()
    # ablation §17.2 condition A ("first failed submission ends the episode")
    # is opt-in per run via env, so Full-style runs keep official semantics
    ablation_a = os.environ.get("BIRD_ABLATION_CONDITION", "").strip().lower() == "a"
    app = create_app(
        BirdSystemServerAdapter(factory=factory, stop_on_submit_fail=ablation_a),
        on_shutdown=factory.aclose,
    )
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("SYSTEM_AGENT_PORT", "6000")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
