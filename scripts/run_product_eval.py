"""Run the §17.1 product question evaluation (paid gate; authorization required).

For each visible question and each condition (A full catalog, B thresholded
BM25 retrieval), renders the retail sql-generate context, requests one SQL
candidate from the model, executes agent and reference SQL through the
QueryEngine, and writes a per-question JSONL report plus per-condition
summary. Gold rows are re-executed live and cross-checked against the bank's
stored digest before any model call.

Usage:
  uv run --env-file .env python scripts/run_product_eval.py \
    --condition a --experiment product-eval-20260917-a \
    [--sets development regression] [--limit N] [--out outputs/product-eval]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from pydantic import SecretStr

from commerce_agent.context_builder.builder import ContextBuilder, compute_config_hash
from commerce_agent.context_builder.profiles import ProfileRegistry
from commerce_agent.knowledge.contracts import KnowledgeEvidence
from commerce_agent.knowledge.retrieval import (
    build_corpus,
    load_glossary,
    retrieve_metadata,
)
from commerce_agent.model._retry import RetryPolicy
from commerce_agent.model._turn_store import InMemoryProviderTurnStore
from commerce_agent.model.gateway import DeepSeekModelGateway
from commerce_agent.model.snapshots import load_reviewed_model_snapshots
from commerce_agent.product_eval.question_eval import (
    build_question_context,
    parse_sql_candidate,
    score_execution,
)
from commerce_agent.product_eval.scoring import result_digest
from commerce_agent.query_engine._ast_policy import AstPolicy
from commerce_agent.query_engine._postgres import PostgresExecutor
from commerce_agent.query_engine.contracts import QueryRequest
from commerce_agent.query_engine.engine import QueryEngine


def _load_questions(paths: list[Path]) -> list[dict]:
    questions: list[dict] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                questions.append(json.loads(line))
    return questions


def _catalog_evidence(config_root: Path) -> tuple[KnowledgeEvidence, ...]:
    payload = json.loads(
        (config_root.parent.parent / "data" / "knowledge" / "retail_catalog.v1.json")
        .read_text(encoding="utf-8")
    )
    return tuple(
        KnowledgeEvidence(
            doc_id=item["doc_id"],
            revision=payload["revision_id"],
            kind=item["kind"],
            title=item["title"],
            content=item["content"],
            source_type="catalog",
            source_path="data/knowledge/retail_catalog.v1.json",
            source_sha256="0" * 64,
        )
        for item in payload["documents"]
    )


async def _run(args: argparse.Namespace) -> int:
    repo = Path(__file__).resolve().parents[1]
    config_root = repo / "configs" / "model"
    registry = ProfileRegistry.load(config_root)
    profile = registry.get("retail")
    inference = next(rule.inference for rule in profile.inference_rules)
    config_hash = compute_config_hash(profile, inference, "deepseek-flash")

    dsn = os.environ["PRODUCT_DATABASE_DSN"]
    engine = QueryEngine(
        policy=AstPolicy(), executor=PostgresExecutor(SecretStr(dsn))
    )
    gold_conn = psycopg.connect(dsn, connect_timeout=10)

    evidence = _catalog_evidence(config_root)
    glossary = load_glossary(repo / "data" / "product-eval" / "glossary-zh.json")
    corpus = build_corpus(evidence, glossary)

    questions = _load_questions(
        [repo / "data" / "product-eval" / f"{name}.jsonl" for name in args.sets]
    )
    if args.limit:
        questions = questions[: args.limit]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"product-eval-{args.experiment}.jsonl"

    builder = ContextBuilder(
        registry=registry,
        estimator=_TokenizerEstimator(),
        requested_model="deepseek-flash",
        timeout_seconds=60,
        provider_user_id="0" * 32,
    )

    api_key = os.environ["DEEPSEEK_API_KEY"]
    base_url = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com")
    model_snapshots = load_reviewed_model_snapshots(config_root)
    clock = _Clock()
    async with httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=120.0,
    ) as client:
        gateway = DeepSeekModelGateway(
            client=client,
            turn_store=InMemoryProviderTurnStore(clock=clock),
            capability=model_snapshots.capability,
            prices=model_snapshots.prices,
            retry_policy=RetryPolicy(maximum_attempts=1),
            clock=clock,
            sleeper=lambda _seconds: asyncio.sleep(0),
            jitter_rng=lambda: Decimal(0),
        )

        total_cost = 0.0
        written = 0
        with report_path.open("w", encoding="utf-8", newline="\n") as report:
            for condition in ("a", "b") if args.condition == "ab" else (args.condition,):
                for item in questions:
                    started = time.monotonic()
                    error_class = None
                    agent_rows = None
                    sql = None
                    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                    cost_usd = None
                    gold_rows, recall, hits = None, 0.0, ()
                    try:
                        with gold_conn.cursor() as cur:
                            cur.execute(item["gold_sql"])
                            gold_columns = [d[0] for d in cur.description]
                            gold_rows = [
                                dict(zip(gold_columns, row)) for row in cur.fetchall()
                            ]
                        digest = result_digest(gold_rows)
                        if digest != item["gold_result_sha256"]:
                            error_class = "gold_digest_mismatch"
                        recall, hits = _recall(
                            condition=condition,
                            question=item["question"],
                            corpus=corpus,
                            gold_tables=item["gold_tables"],
                        )
                    except Exception as error:  # noqa: BLE001
                        error_class = f"gold_{type(error).__name__}"
                    if error_class is None:
                        try:
                            context = build_question_context(
                                question=item["question"],
                                condition=condition,
                                evidence=evidence,
                                glossary=glossary,
                                registry=registry,
                                builder=builder,
                                run_scope=_scope(args.experiment, condition, config_hash, item["question_id"]),
                                attempt_id=uuid4(),
                            )
                            response = await gateway.complete(context.model_request)
                            usage = _usage(response)
                            cost = response.cost
                            cost_usd = Decimal(str(cost.amount)) if cost else None
                            sql, _ = parse_sql_candidate(response.output)
                            result = await engine.execute(QueryRequest(sql=sql))
                            agent_rows = result.rows
                        except Exception as error:  # noqa: BLE001
                            error_class = f"agent_{type(error).__name__}"
                    score = score_execution(
                        question_id=item["question_id"],
                        condition=condition,
                        agent_rows=agent_rows,
                        gold_rows=gold_rows,
                        reference_columns=gold_columns,
                        row_order=item.get("row_order", "unordered"),
                        recall=recall,
                        row_count=len(agent_rows or []),
                        usage=usage,
                        cost_usd=cost_usd,
                        latency_seconds=round(time.monotonic() - started, 3),
                        error_class=error_class,
                    )
                    if cost_usd is not None:
                        total_cost += float(cost_usd)
                    report.write(
                        json.dumps(
                            {
                                **score.model_dump(mode="json"),
                                "sql": sql,
                                "retrieved_doc_ids": [h.doc.doc_id for h in hits],
                                "gold_row_count": len(gold_rows),
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n"
                    )
                    report.flush()
                    written += 1
        gold_conn.close()
        print(f"report: {report_path} ({written} rows); agent-side total ${total_cost:.6f}")
    return 0


class _Clock:
    def __init__(self) -> None:
        self._now = datetime.now(UTC)

    def now(self) -> datetime:
        self._now = datetime.now(UTC)
        return self._now


class _TokenizerEstimator:
    @property
    def revision(self) -> str:
        return "deepseek-tokenizer-v1"

    def estimate(self, request: object) -> object:
        from commerce_agent.context_builder._tokens import TokenEstimate

        return TokenEstimate(input_tokens=100, estimator_revision=self.revision)


def _snapshots(config_root: Path):
    return load_reviewed_model_snapshots(config_root)


def _usage(response) -> dict[str, int]:
    usage = response.usage
    payload = usage.model_dump(mode="json") if hasattr(usage, "model_dump") else dict(usage)
    return {
        "prompt_tokens": int(payload.get("prompt_tokens", 0)),
        "completion_tokens": int(payload.get("completion_tokens", 0)),
        "total_tokens": int(payload.get("total_tokens", 0)),
    }


def _recall(*, condition: str, question: str, corpus, gold_tables: list[str]):
    from commerce_agent.knowledge.retrieval import (
        full_catalog_table_recall,
        gold_table_recall,
    )

    if condition == "a":
        return full_catalog_table_recall(corpus, gold_tables), ()
    hits = retrieve_metadata(question, corpus)
    return gold_table_recall(hits, gold_tables), hits


def _scope(experiment: str, condition: str, config_hash: str, subject_id: str):
    from commerce_agent.model.contracts import RunScope

    return RunScope(
        run_id=uuid4(),
        track="retail",
        mode="retail",
        subject_id=subject_id,
        experiment_id=experiment,
        config_hash=config_hash,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="§17.1 product question evaluation")
    parser.add_argument("--condition", choices=("a", "b", "ab"), required=True)
    parser.add_argument("--experiment", required=True)
    parser.add_argument(
        "--sets", nargs="+", default=["development", "regression"]
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default="outputs/product-eval")
    args = parser.parse_args()
    # pit 59: psycopg async rejects the win32 Proactor loop; run on Selector
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
