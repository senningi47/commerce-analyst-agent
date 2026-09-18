"""§17.1 product question evaluation harness.

Builds the per-question context for condition A (full catalog metadata) or
condition B (thresholded BM25 retrieval — §10.2 route 2), renders it through
the frozen retail `retail_sql_generate` step, parses the model's
`submit_sql_candidate` call, and scores the executed result against the
question's reference query (multiset row equality from scoring.py) plus Gold
table Recall@5. Usage, cost and latency ride on the gateway response; no
component here is model-provider-specific.
"""

from __future__ import annotations

import json
from decimal import Decimal
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict

from commerce_agent.context_builder.builder import ContextBuilder
from commerce_agent.context_builder.contracts import (
    ContextDatum,
    ContextRequest,
    DataNamespace,
    DatumKind,
    PromptStep,
)
from commerce_agent.context_builder.profiles import ProfileRegistry
from commerce_agent.knowledge.contracts import KnowledgeEvidence
from commerce_agent.knowledge.retrieval import (
    CorpusDoc,
    RetrievalHit,
    build_corpus,
    full_catalog_table_recall,
    gold_table_recall,
    retrieve_metadata,
)
from commerce_agent.model.contracts import (
    ModelRequest,
    RunScope,
    ToolCallOutput,
)
from commerce_agent.product_eval.scoring import results_match

Condition = Literal["a", "b"]


class QuestionContextError(RuntimeError):
    pass


class SqlCandidateError(RuntimeError):
    pass


class QuestionContext(BaseModel, frozen=True):
    condition: Condition
    model_request: ModelRequest
    evidence_doc_ids: tuple[str, ...]


def _datum_for(evidence: KnowledgeEvidence, revision: str) -> ContextDatum:
    kind = (
        DatumKind.BUSINESS_VALUE
        if evidence.doc_id.startswith("metric.")
        else DatumKind.KNOWLEDGE
    )
    namespace = (
        DataNamespace.BUSINESS_VALUE
        if evidence.doc_id.startswith("metric.")
        else DataNamespace.RETAIL_KNOWLEDGE
    )
    content = json.dumps(evidence.content, ensure_ascii=False, sort_keys=True)
    return ContextDatum(
        kind=kind,
        namespace=namespace,
        source_ref=evidence.doc_id,
        revision=revision,
        content=content,
        digest=sha256(content.encode("utf-8")).hexdigest(),
    )


def build_question_context(
    *,
    question: str,
    condition: Condition,
    evidence: tuple[KnowledgeEvidence, ...],
    glossary: tuple,
    registry: ProfileRegistry,
    builder: ContextBuilder,
    run_scope: RunScope,
    attempt_id: object,
) -> QuestionContext:
    """Render one question's context: condition A embeds the full catalog,
    condition B embeds only the thresholded top-5 retrieval hits."""
    corpus = build_corpus(evidence, glossary)
    hits: tuple[RetrievalHit, ...] = ()
    if condition == "b":
        hits = retrieve_metadata(question, corpus)
        selected_ids = {hit.doc.doc_id for hit in hits}
    else:
        selected_ids = {doc.doc_id for doc in corpus}
    if not selected_ids:
        raise QuestionContextError("empty_context")
    revision = evidence[0].revision if evidence else "retail-catalog-v1"
    data = tuple(
        _datum_for(item, revision) for item in evidence if item.doc_id in selected_ids
    )
    bundle = builder.build(
        ContextRequest(
            run_scope=run_scope,
            attempt_id=attempt_id,  # type: ignore[arg-type]
            sequence=0,
            profile=registry.get("retail"),
            step=PromptStep.RETAIL_SQL_GENERATE,
            current_input=ContextDatum(
                kind=DatumKind.USER_INPUT,
                namespace=DataNamespace.USER_INPUT,
                source_ref=f"product-question:{run_scope.subject_id}",
                content=question,
                digest=sha256(question.encode("utf-8")).hexdigest(),
            ),
            evidence=data,
        )
    )
    return QuestionContext(
        condition=condition,
        model_request=bundle.model_request,
        evidence_doc_ids=tuple(item.doc_id for item in evidence if item.doc_id in selected_ids),
    )


def recall_for_tables(
    *, condition: Condition, question: str, corpus: tuple[CorpusDoc, ...], gold_tables: list[str]
) -> tuple[float, tuple[RetrievalHit, ...]]:
    """Recall@5 for the question's gold tables under the given condition."""
    if condition == "a":
        return full_catalog_table_recall(corpus, gold_tables), ()
    hits = retrieve_metadata(question, corpus)
    return gold_table_recall(hits, gold_tables), hits


def parse_sql_candidate(output: object) -> tuple[str, tuple[str, ...]]:
    """Extract (sql, evidence_refs) from the model's single
    submit_sql_candidate tool call."""
    if not isinstance(output, ToolCallOutput) or len(output.tool_calls) != 1:
        raise SqlCandidateError("typed_sql_candidate_required")
    call = output.tool_calls[0]
    if call.name != "submit_sql_candidate":
        raise SqlCandidateError("tool_not_allowed_for_step")
    try:
        arguments = json.loads(call.arguments_json)
    except json.JSONDecodeError as error:
        raise SqlCandidateError("invalid_candidate_arguments") from error
    if not isinstance(arguments, dict) or not isinstance(arguments.get("sql"), str):
        raise SqlCandidateError("sql_missing")
    refs = arguments.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise SqlCandidateError("evidence_refs_missing")
    sql = arguments["sql"]
    # codex F10: read-only enforcement belongs to the QueryEngine AST policy
    # (which also accepts CTEs/comments this string check rejected) — the
    # harness only parses the response contract
    if not sql.strip():
        raise SqlCandidateError("sql_missing")
    return sql, tuple(str(ref) for ref in refs)


class QuestionScore(BaseModel, frozen=True):
    question_id: str
    condition: Condition
    correct: bool
    executed: bool
    error_class: str | None = None
    gold_table_recall: float
    row_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: Decimal | None
    latency_seconds: float

    model_config = ConfigDict(frozen=True)


def score_execution(
    *,
    question_id: str,
    condition: Condition,
    agent_rows: list[dict] | None,
    gold_rows: list[dict],
    reference_columns: list[str],
    row_order: str = "unordered",
    recall: float,
    row_count: int,
    usage: dict[str, int],
    cost_usd: Decimal | None,
    latency_seconds: float,
    error_class: str | None = None,
) -> QuestionScore:
    correct = bool(
        agent_rows is not None
        and results_match(
            agent_rows,
            gold_rows,
            reference_columns,
            ordered=row_order == "ascending",
        )
    )
    return QuestionScore(
        question_id=question_id,
        condition=condition,
        correct=correct,
        executed=agent_rows is not None,
        error_class=error_class,
        gold_table_recall=recall,
        row_count=row_count,
        prompt_tokens=int(usage.get("prompt_tokens", 0)),
        completion_tokens=int(usage.get("completion_tokens", 0)),
        total_tokens=int(usage.get("total_tokens", 0)),
        cost_usd=cost_usd,
        latency_seconds=latency_seconds,
    )
