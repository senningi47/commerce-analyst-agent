"""Minimal FastAPI factory for the Day 6 product API."""

from __future__ import annotations

from fastapi import FastAPI

from commerce_agent.api.events import RunEventSource
from commerce_agent.api.sse import build_events_router


def create_app(
    *,
    event_source: RunEventSource,
    poll_interval: float = 1.0,
    heartbeat_interval: float = 15.0,
) -> FastAPI:
    app = FastAPI(title="commerce-analyst-api")
    app.include_router(
        build_events_router(
            event_source,
            poll_interval=poll_interval,
            heartbeat_interval=heartbeat_interval,
        )
    )
    return app
