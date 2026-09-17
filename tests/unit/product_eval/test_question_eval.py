"""Unit tests for the §17.1 question-eval harness (no model calls)."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import JsonValue

from commerce_agent.context_builder.builder import ContextBuilder
from commerce_agent.context_builder.profiles import ProfileRegistry
from commerce_agent.knowledge.contracts import KnowledgeEvidence
from commerce_agent.model.contracts import RunScope, ToolCall, ToolCallOutput
from commerce_agent.product_eval.question_eval import (
    SqlCandidateError,
    build_question_context,
    parse_sql_candidate,
    score_execution,
)

CONFIG_ROOT = Path(__file__).parents[3] / "configs" / "model"


def _doc(doc_id: str, title: str, content: dict[str, JsonValue]) -> KnowledgeEvidence:
    return KnowledgeEvidence(
        doc_id=doc_id,
        revision="retail-catalog-v1",
        kind="metric" if doc_id.startswith("metric") else "table",
        title=title,
        content=content,
        source_type="catalog",
        source_path=f"data/knowledge#{doc_id}",
        source_sha256="a" * 64,
    )


class _G:
    def __init__(self, glossary_id: str, terms: tuple[str, ...], target_doc_id: str):
        self.glossary_id = glossary_id
        self.terms = terms
        self.target_doc_id = target_doc_id


@pytest.fixture
def evidence() -> tuple[KnowledgeEvidence, ...]:
    return (
        _doc(
            "table.order_payments",
            "retail.order_payments",
            {"columns": ["order_id", "payment_type", "payment_value"]},
        ),
        _doc("table.orders", "retail.orders", {"columns": ["order_id", "order_status"]}),
        _doc(
            "metric.payment_amount",
            "payment_amount",
            {"definition": "SUM(order_payments.payment_value)"},
        ),
    )


@pytest.fixture
def glossary() -> tuple:
    return (_G("g.pay", ("支付", "支付金额"), "table.order_payments"),)


def _builder() -> ContextBuilder:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    return ContextBuilder(
        registry=registry,
        estimator=_FixedEstimator(),
        requested_model="deepseek-flash",
        timeout_seconds=30,
        provider_user_id="0" * 32,
    )


class _FixedEstimator:
    @property
    def revision(self) -> str:
        return "deepseek-tokenizer-v1"

    def estimate(self, request: object) -> object:
        from commerce_agent.context_builder._tokens import TokenEstimate

        return TokenEstimate(input_tokens=100, estimator_revision=self.revision)


def _scope() -> RunScope:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    profile = registry.get("retail")
    inference = next(rule.inference for rule in profile.inference_rules)
    from commerce_agent.context_builder.builder import compute_config_hash

    return RunScope(
        run_id=uuid4(),
        track="retail",
        mode="retail",
        subject_id="q-dev-01",
        experiment_id="product-eval-test",
        config_hash=compute_config_hash(profile, inference, "deepseek-flash"),
    )


def test_condition_a_embeds_full_catalog(evidence, glossary) -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    context = build_question_context(
        question="各支付方式的支付金额是多少？",
        condition="a",
        evidence=evidence,
        glossary=glossary,
        registry=registry,
        builder=_builder(),
        run_scope=_scope(),
        attempt_id=uuid4(),
    )
    assert set(context.evidence_doc_ids) == {d.doc_id for d in evidence}
    request = context.model_request
    rendered = [m.content for m in request.messages if m.role == "user"]
    embedded = "".join(rendered)
    assert "payment_value" in embedded


def test_condition_b_embeds_retrieved_subset(evidence, glossary) -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    context = build_question_context(
        question="各支付方式的支付金额是多少？",
        condition="b",
        evidence=evidence,
        glossary=glossary,
        registry=registry,
        builder=_builder(),
        run_scope=_scope(),
        attempt_id=uuid4(),
    )
    assert 0 < len(context.evidence_doc_ids) < len(evidence)
    assert "table.orders" not in context.evidence_doc_ids  # payment question


def test_parse_sql_candidate_accepts_valid_call() -> None:
    arguments = {
        "type": "submit_sql_candidate",
        "evidence_refs": ["table.order_payments"],
        "sql": "SELECT 1",
        "step_id": "q.step1",
    }
    output = ToolCallOutput(
        type="tool_calls",
        tool_calls=[
            ToolCall(
                call_id="call-1",
                name="submit_sql_candidate",
                arguments_json=json.dumps(arguments, sort_keys=True, separators=(",", ":")),
            )
        ],
    )
    sql, refs = parse_sql_candidate(output)
    assert sql == "SELECT 1"
    assert refs == ("table.order_payments",)


def test_parse_sql_candidate_rejects_wrong_tool_and_non_select() -> None:
    output = ToolCallOutput(
        type="tool_calls",
        tool_calls=[ToolCall(call_id="call-1", name="get_schema", arguments_json="{}")],
    )
    with pytest.raises(SqlCandidateError):
        parse_sql_candidate(output)

    bad = ToolCall(
        call_id="call-2",
        name="submit_sql_candidate",
        arguments_json=json.dumps(
            {
                "type": "submit_sql_candidate",
                "evidence_refs": ["x"],
                "sql": "DELETE FROM t",
                "step_id": "s1",
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    with pytest.raises(SqlCandidateError):
        parse_sql_candidate(ToolCallOutput(type="tool_calls", tool_calls=[bad]))


def test_score_execution_compares_multisets() -> None:
    score = score_execution(
        question_id="q1",
        condition="a",
        agent_rows=[{"amt": 10.5}],
        gold_rows=[{"total": "10.500000"}],
        recall=1.0,
        row_count=1,
        usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        cost_usd=None,
        latency_seconds=0.4,
    )
    assert score.correct is True
    miss = score_execution(
        question_id="q1",
        condition="a",
        agent_rows=[{"amt": 11}],
        gold_rows=[{"total": "10.5"}],
        recall=1.0,
        row_count=1,
        usage={},
        cost_usd=None,
        latency_seconds=0.4,
    )
    assert miss.correct is False
