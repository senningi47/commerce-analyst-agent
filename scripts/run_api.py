"""Launcher for the Day 6 read-only API.

Run with the reviewed env: ``uv run --env-file .env python scripts/run_api.py``.

Loop discipline (win32): uvicorn 0.52 hard-pins ``asyncio.ProactorEventLoop``
as its loop factory when not running workers/reload, and psycopg async
refuses Proactor - so the policy switch and the in-factory patch are both
too late. We drive ``Server.serve`` ourselves on a ``SelectorEventLoop``
factory, mirroring uvicorn's own ``asyncio_run``. See HANDOFF pitfall 59.
"""

import asyncio
import os

import uvicorn

from commerce_agent.api.app import create_postgres_app


def main() -> None:
    host = os.environ.get("COMMERCE_AGENT_API_HOST", "127.0.0.1")
    port = int(os.environ.get("COMMERCE_AGENT_API_PORT", "8010"))
    app = create_postgres_app()
    config = uvicorn.Config(app, host=host, port=port)
    server = uvicorn.Server(config)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(server.serve())


if __name__ == "__main__":
    main()
