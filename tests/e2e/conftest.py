"""E2E conftest: win32 selector loop + the postgres skip gate.

Mirrors the two load-bearing pieces of tests/integration/conftest.py that
the e2e suite needs (the marker-based skip logic lives per-package; see
HANDOFF pitfall 59 for why the selector policy is mandatory on win32).
"""

import asyncio
import os
import sys

import pytest


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    del config
    run_postgres = os.environ.get("COMMERCE_AGENT_RUN_POSTGRES_TESTS") == "1"
    skip_postgres = pytest.mark.skip(reason="set COMMERCE_AGENT_RUN_POSTGRES_TESTS=1")
    for item in items:
        if "postgres" in item.keywords and not run_postgres:
            item.add_marker(skip_postgres)
