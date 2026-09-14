"""Minimal FastAPI factory for the Day 6 product API.

Serving on win32 requires the selector event loop (psycopg async refuses
Proactor) - use ``scripts/run_api.py``, which drives uvicorn on a
``SelectorEventLoop`` factory; uvicorn 0.52 would otherwise pin Proactor.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from pydantic import SecretStr

from commerce_agent.api.eval import EvalRecordSource, PostgresEvalSource, build_eval_router
from commerce_agent.api.events import RunEventSource
from commerce_agent.api.runs import PostgresRunDirectory, RunDirectory, build_runs_router
from commerce_agent.api.sse import PostgresTraceEventSource, build_events_router


def create_app(
    *,
    event_source: RunEventSource,
    eval_source: EvalRecordSource | None = None,
    run_directory: RunDirectory | None = None,
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
    if eval_source is not None:
        app.include_router(build_eval_router(eval_source))
    if run_directory is not None:
        app.include_router(build_runs_router(run_directory))
    return app


def create_postgres_app(*, dsn: SecretStr | None = None) -> FastAPI:
    """Env-wired assembly: every read side speaks the same agent_reader identity.

    ``uv run --env-file .env python scripts/run_api.py`` serves the whole
    surface; the DSN defaults to ``PRODUCT_DATABASE_DSN``.
    """

    resolved = dsn or SecretStr(os.environ["PRODUCT_DATABASE_DSN"])
    return create_app(
        event_source=PostgresTraceEventSource(resolved),
        eval_source=PostgresEvalSource(resolved),
        run_directory=PostgresRunDirectory(resolved),
    )
