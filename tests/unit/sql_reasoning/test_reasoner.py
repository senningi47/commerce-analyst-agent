import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from commerce_agent.context_builder.contracts import ContextDatum
from commerce_agent.context_builder.profiles import ProfileRegistry
from commerce_agent.model.contracts import (
    ChatMessage,
    CostUnavailable,
    ModelAttemptSummary,
    ModelRequest,
    ModelResponse,
    NonThinkingConfig,
    ProviderTurnRef,
    ReportedUsage,
    RunScope,
    ToolCall,
    ToolCallOutput,
)
from commerce_agent.model.fake import FakeModel
from commerce_agent.sql_reasoning.contracts import ProductDbError, SqlReasoningRequest
from commerce_agent.sql_reasoning.errors import SqlNoProgress
from commerce_agent.sql_reasoning.reasoner import SqlReasoner

CONFIG_ROOT = Path(__file__).parents[3] / "configs" / "model"
NOW = datetime(2026, 9, 6, 4, 0, tzinfo=UTC)


def usage_and_cost():
    usage = ReportedUsage(
        status="reported",
        prompt_tokens=10,
        cache_hit_tokens=0,
        cache_miss_tokens=10,
        completion_tokens=5,
        reasoning_tokens=0,
        total_tokens=15,
    )
    cost = CostUnavailable(status="unavailable", reason_code="fixture_price_unavailable")
    return usage, cost


def sql_candidate_response(sql: str, *, step_id: str = "baseline") -> ModelResponse:
    usage, cost = usage_and_cost()
    call = ToolCall(
        call_id="sql-1",
        name="submit_sql_candidate",
        arguments_json=json.dumps(
            {
                "evidence_refs": ["knowledge:metric.orders:v1"],
                "sql": sql,
                "step_id": step_id,
                "type": "submit_sql_candidate",
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    return ModelResponse(
        output=ToolCallOutput(type="tool_calls", tool_calls=(call,)),
        provider_turn_ref=ProviderTurnRef(
            turn_id=UUID(int=9),
            scope_digest="c" * 64,
            attempt_id=UUID(int=2),
            sequence=0,
            payload_sha256="d" * 64,
            expected_tool_call_ids=("sql-1",),
            token_weight=5,
            expires_at=NOW,
        ),
        finish_reason="tool_calls",
        actual_model="fake",
        usage=usage,
        cost=cost,
        attempts=(
            ModelAttemptSummary(
                attempt_number=1,
                sent_at=NOW,
                completed_at=NOW,
                outcome="success",
                http_status=200,
                retryable=False,
                charge_ambiguous=False,
                usage=usage,
                cost=cost,
            ),
        ),
    )


class StubContextBuilder:
    def build(self, request):
        model_request = ModelRequest(
            run_scope=request.run_scope,
            attempt_id=request.attempt_id,
            sequence=request.sequence,
            inference=NonThinkingConfig(
                type="disabled", temperature=0, max_output_tokens=4096
            ),
            messages=(ChatMessage(role="user", content="generate SQL"),),
            timeout_seconds="30",
            capability_revision="test",
            provider_user_id="e" * 32,
            prompt_policy_hash="1" * 64,
            rendered_prompt_hash="2" * 64,
            tool_hash="3" * 64,
            context_hash="4" * 64,
            config_hash=request.run_scope.config_hash,
        )
        return SimpleNamespace(model_request=model_request)


def datum(content: str, source_ref: str) -> ContextDatum:
    return ContextDatum(
        kind="knowledge" if source_ref.startswith("knowledge:") else "user_input",
        namespace="retail_knowledge" if source_ref.startswith("knowledge:") else "user_input",
        source_ref=source_ref,
        revision="v1" if source_ref.startswith("knowledge:") else None,
        content=content,
        digest=sha256(content.encode()).hexdigest(),
    )


def sql_reasoning_request(
    *, previous=None, latest_error: ProductDbError | None = None, repair_number: int = 0
) -> SqlReasoningRequest:
    return SqlReasoningRequest(
        run_scope=RunScope(
            run_id=UUID(int=1),
            track="retail",
            mode="retail",
            subject_id="day4",
            experiment_id="product",
            config_hash="a" * 64,
        ),
        attempt_id=UUID(int=2),
        sequence=0,
        step_id="baseline",
        current_input=datum("Count delivered orders.", "request:1"),
        evidence=(datum("Orders metric definition.", "knowledge:metric.orders:v1"),),
        profile_key="retail",
        repair_number=repair_number,
        latest_error=latest_error,
        previous=previous,
    )


@pytest.mark.asyncio
async def test_reasoner_generates_one_bound_candidate_and_reports_usage() -> None:
    gateway = FakeModel(
        [sql_candidate_response("SELECT COUNT(*) AS order_count FROM retail.orders")]
    )
    reasoner = SqlReasoner(
        context_builder=StubContextBuilder(),
        profiles=ProfileRegistry.load(CONFIG_ROOT),
        gateway=gateway,
    )

    candidate = await reasoner.generate(sql_reasoning_request())

    assert candidate.sql == "SELECT COUNT(*) AS order_count FROM retail.orders"
    assert candidate.step_id == "baseline"
    assert candidate.repair_number == 0
    assert candidate.structural_fingerprint.algorithm_version == "sql-fingerprint-v1"
    assert candidate.execution_fingerprint.digest != "0" * 64
    assert len(gateway.requests) == 1


@pytest.mark.asyncio
async def test_same_execution_and_same_evidence_stops_without_another_repair() -> None:
    sql = "SELECT COUNT(*) FROM retail.orders"
    first_gateway = FakeModel([sql_candidate_response(sql)])
    profiles = ProfileRegistry.load(CONFIG_ROOT)
    first = await SqlReasoner(
        context_builder=StubContextBuilder(), profiles=profiles, gateway=first_gateway
    ).generate(sql_reasoning_request())
    gateway = FakeModel([sql_candidate_response(sql)])
    reasoner = SqlReasoner(
        context_builder=StubContextBuilder(), profiles=profiles, gateway=gateway
    )

    with pytest.raises(SqlNoProgress) as caught:
        await reasoner.generate(
            sql_reasoning_request(
                previous=first,
                latest_error=ProductDbError(
                    source="postgres",
                    reason_code="undefined_column",
                    retryable=True,
                    evidence_digest="b" * 64,
                ),
                repair_number=1,
            )
        )

    assert caught.value.reason_code == "sql_no_progress"
