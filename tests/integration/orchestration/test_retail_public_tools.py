import json
import os
from uuid import UUID

import pytest
from pydantic import SecretStr

from commerce_agent.knowledge._postgres import PostgresKnowledgeStore
from commerce_agent.knowledge.module import KnowledgeModule
from commerce_agent.model.contracts import RunScope, ToolCall
from commerce_agent.orchestration.tools import RetailToolDispatcher
from commerce_agent.query_engine._ast_policy import AstPolicy
from commerce_agent.query_engine._postgres import PostgresExecutor
from commerce_agent.query_engine.engine import QueryEngine
from commerce_agent.value_resolver._postgres import PostgresValueStore
from commerce_agent.value_resolver.resolver import BusinessValueResolver

pytestmark = pytest.mark.postgres
ATTEMPT_ID = UUID("00000000-0000-0000-0000-000000000822")


def retail_scope() -> RunScope:
    return RunScope(
        run_id=UUID("00000000-0000-0000-0000-000000000821"),
        track="retail",
        mode="retail",
        subject_id="retail-public-tools",
        experiment_id="day3",
        config_hash="a" * 64,
    )


def tool_call(call_id: str, name: str, arguments: dict[str, str]) -> ToolCall:
    return ToolCall(
        call_id=call_id,
        name=name,
        arguments_json=json.dumps(arguments, sort_keys=True, separators=(",", ":")),
    )


@pytest.mark.asyncio
async def test_retail_dispatcher_composes_all_three_product_public_modules() -> None:
    dispatcher = RetailToolDispatcher(
        knowledge=KnowledgeModule(
            PostgresKnowledgeStore(
                SecretStr(os.environ["PRODUCT_KNOWLEDGE_DATABASE_DSN"])
            )
        ),
        resolver=BusinessValueResolver(
            PostgresValueStore(SecretStr(os.environ["PRODUCT_DATABASE_DSN"]))
        ),
        query_engine=QueryEngine(
            policy=AstPolicy(),
            executor=PostgresExecutor(SecretStr(os.environ["PRODUCT_DATABASE_DSN"])),
        ),
    )
    scope = retail_scope()

    knowledge = await dispatcher.execute(
        scope,
        ATTEMPT_ID,
        tool_call(
            "call_knowledge",
            "retrieve_retail_knowledge",
            {"question": "What does GMV mean?"},
        ),
    )
    resolution = await dispatcher.execute(
        scope,
        ATTEMPT_ID,
        tool_call(
            "call_resolver",
            "resolve_business_value",
            {"domain": "order_status", "raw_text": "delivered"},
        ),
    )
    query = await dispatcher.execute(
        scope,
        ATTEMPT_ID,
        tool_call(
            "call_query",
            "execute_readonly_sql",
            {
                "sql": (
                    "SELECT COUNT(DISTINCT order_status) AS status_count "
                    "FROM retail.orders"
                )
            },
        ),
    )

    knowledge_payload = json.loads(knowledge.content_json)
    gmv = next(
        item for item in knowledge_payload["evidence"] if item["doc_id"] == "metric.gmv"
    )
    assert gmv["content"]["status"] == "clarification_required"
    assert json.loads(resolution.content_json)["status"] == "resolved"
    query_payload = json.loads(query.content_json)
    assert query_payload["columns"] == ["status_count"]
    assert query_payload["row_count"] == 1

    encoded = (
        f"{knowledge.content_json} {resolution.content_json} {query.content_json}"
    ).casefold()
    assert "bird" not in encoded
    assert "5433" not in encoded
    assert "6002" not in encoded
