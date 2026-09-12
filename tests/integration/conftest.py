import asyncio
import os
import sys
from datetime import UTC, datetime
from uuid import UUID

import psycopg
import pytest
from pydantic import SecretStr

from commerce_agent.operations._approval import (
    ApprovalService,
    HmacApprovalKeyring,
    SecretsNonceSource,
    SystemClock,
)
from commerce_agent.operations._postgres import PostgresOperationStore
from commerce_agent.operations._store import DefaultReferenceValidator
from commerce_agent.operations.contracts import ActorContext, ActorRole
from commerce_agent.operations.workflow import OperationWorkflow
from commerce_agent.product_eval._reset import PostgresScenarioReset

PRODUCT_APPLICATION_NAMES = (
    "commerce_query_engine",
    "commerce_value_resolver",
    "commerce_knowledge_reader",
    "commerce_model_state",
    "commerce_retail_checkpoint",
    "commerce_operation_proposal",
    "commerce_operation_approval",
    "commerce_operation_execute",
    "commerce_product_trace",
    "commerce_product_scenario_reset",
    "commerce_evaluation_runner",
)


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


@pytest.fixture(autouse=True)
async def assert_product_sessions_are_closed(request: pytest.FixtureRequest) -> None:
    if (
        request.node.get_closest_marker("postgres") is None
        or os.environ.get("COMMERCE_AGENT_RUN_POSTGRES_TESTS") != "1"
    ):
        yield
        return
    yield

    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE application_name = ANY(%s)",
            (
                list(PRODUCT_APPLICATION_NAMES),
            ),
        )
        assert (await cursor.fetchone())[0] == 0
    finally:
        await connection.close()


@pytest.fixture
def product_scenario_id() -> str:
    return "task15-postgres-integration-v1"


@pytest.fixture
def postgres_scenario_reset() -> PostgresScenarioReset:
    return PostgresScenarioReset(
        SecretStr(os.environ["PRODUCT_SCENARIO_RESET_DATABASE_DSN"])
    )


@pytest.fixture
async def clean_product_scenario(
    postgres_scenario_reset: PostgresScenarioReset,
    product_scenario_id: str,
) -> None:
    await postgres_scenario_reset.reset(product_scenario_id, "scenario-reset-v1")
    yield
    await postgres_scenario_reset.reset(product_scenario_id, "scenario-reset-v1")


@pytest.fixture
def postgres_operation_store(product_scenario_id: str) -> PostgresOperationStore:
    return PostgresOperationStore(
        proposal_dsn=SecretStr(os.environ["PRODUCT_PROPOSAL_DATABASE_DSN"]),
        approval_dsn=SecretStr(os.environ["PRODUCT_APPROVAL_DATABASE_DSN"]),
        execution_dsn=SecretStr(os.environ["PRODUCT_OPERATION_DATABASE_DSN"]),
        scenario_id=product_scenario_id,
    )


@pytest.fixture
def postgres_workflow(
    postgres_operation_store: PostgresOperationStore,
) -> OperationWorkflow:
    return OperationWorkflow(
        store=postgres_operation_store,
        references=DefaultReferenceValidator(),
        clock=SystemClock(),
        approvals=ApprovalService(
            clock=SystemClock(),
            nonce_source=SecretsNonceSource(),
            keyring=HmacApprovalKeyring(
                {1: os.environ["PRODUCT_OPERATION_APPROVAL_HMAC_KEY_V1"].encode("utf-8")},
                active_version=1,
            ),
        ),
    )


@pytest.fixture
def analyst_actor() -> ActorContext:
    return ActorContext(
        actor_id=UUID("00000000-0000-0000-0000-000000000101"),
        role=ActorRole.ANALYST,
        authentication_ref="integration:analyst",
        authenticated_at=datetime.now(UTC),
    )


@pytest.fixture
def approver_actor() -> ActorContext:
    return ActorContext(
        actor_id=UUID("00000000-0000-0000-0000-000000000102"),
        role=ActorRole.APPROVER,
        authentication_ref="integration:approver",
        authenticated_at=datetime.now(UTC),
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    del config
    run_postgres = os.environ.get("COMMERCE_AGENT_RUN_POSTGRES_TESTS") == "1"
    run_deepseek = os.environ.get("COMMERCE_AGENT_RUN_DEEPSEEK_TESTS") == "1"
    skip_postgres = pytest.mark.skip(reason="set COMMERCE_AGENT_RUN_POSTGRES_TESTS=1")
    skip_deepseek = pytest.mark.skip(reason="set COMMERCE_AGENT_RUN_DEEPSEEK_TESTS=1")
    for item in items:
        if "postgres" in item.keywords and not run_postgres:
            item.add_marker(skip_postgres)
        if "deepseek" in item.keywords and not run_deepseek:
            item.add_marker(skip_deepseek)
