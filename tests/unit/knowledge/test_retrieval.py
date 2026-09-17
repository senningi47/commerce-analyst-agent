"""Unit tests for §10.2 route-2 BM25 metadata retrieval (§17.1 condition B)."""

import json

import pytest
from pydantic import JsonValue

from commerce_agent.knowledge.contracts import KnowledgeEvidence
from commerce_agent.knowledge.retrieval import (
    RETRIEVAL_MIN_RELATIVE_SCORE,
    RETRIEVAL_TOP_K,
    TOKENIZER_REVISION,
    build_corpus,
    full_catalog_table_recall,
    gold_table_recall,
    load_glossary,
    retrieve_metadata,
    tokenize,
)


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
    """Glossary-entry stand-in with the fields retrieval reads."""

    def __init__(self, glossary_id: str, terms: tuple[str, ...], target_doc_id: str):
        self.glossary_id = glossary_id
        self.terms = terms
        self.target_doc_id = target_doc_id


@pytest.fixture
def catalog() -> tuple[KnowledgeEvidence, ...]:
    return (
        _doc(
            "table.order_payments",
            "retail.order_payments",
            {"columns": ["order_id", "payment_type", "payment_value"]},
        ),
        _doc("table.orders", "retail.orders", {"columns": ["order_id", "order_status"]}),
        _doc(
            "table.sellers",
            "retail.sellers",
            {"columns": ["seller_id", "seller_state"]},
        ),
        _doc(
            "metric.payment_amount",
            "payment_amount",
            {"definition": "SUM(order_payments.payment_value)", "currency": "BRL"},
        ),
        _doc("metric.gmv", "gmv", {"status": "clarification_required"}),
        _doc(
            "permission.seller_pii",
            "seller contact fields",
            {"policy": "no seller contact exports"},
        ),
        _doc(
            "data_quality.review_id_non_unique",
            "review_id is not unique",
            {"rows": 99224},
        ),
    )


@pytest.fixture
def glossary() -> tuple:
    return (
        _G("g.pay", ("支付", "付款", "支付金额"), "table.order_payments"),
        _G("g.sellers", ("卖家", "商家"), "table.sellers"),
        _G("g.metric.pay", ("支付金额", "付款金额"), "metric.payment_amount"),
    )


def test_tokenizer_pins_latin_words_and_cjk_chars() -> None:
    assert TOKENIZER_REVISION == "bm25-tokenizer-v1"
    tokens = tokenize("支付金额 Payment Amount!")
    assert tokens == ["支", "付", "金", "额", "payment", "amount"]


def test_chinese_question_matches_glossary_augmented_docs(catalog, glossary) -> None:
    """The zh glossary bridges operator vocabulary onto the latin catalog;
    without it the pure-CJK question cannot lexically match any doc."""
    bare = retrieve_metadata("各支付方式的支付金额是多少", build_corpus(catalog))
    assert bare == ()
    hits = retrieve_metadata(
        "各支付方式的支付金额是多少", build_corpus(catalog, glossary)
    )
    assert hits
    assert hits[0].doc.doc_id in ("table.order_payments", "metric.payment_amount")


def test_retrieval_caps_at_top_k_and_drops_low_scores(catalog, glossary) -> None:
    hits = retrieve_metadata(
        "各支付方式的支付金额是多少", build_corpus(catalog, glossary)
    )
    assert len(hits) <= RETRIEVAL_TOP_K
    if len(hits) > 1:
        assert hits[-1].score >= hits[0].score * RETRIEVAL_MIN_RELATIVE_SCORE


def test_gold_table_recall_and_full_catalog_baseline(catalog, glossary) -> None:
    corpus = build_corpus(catalog, glossary)
    hits = retrieve_metadata("卖家分布", corpus)
    recall = gold_table_recall(hits, ("sellers", "order_payments"))
    assert 0.0 < recall < 1.0
    full = full_catalog_table_recall(corpus, ("sellers", "order_payments"))
    assert full == 1.0  # condition A carries every table document


def test_load_glossary_round_trip(tmp_path) -> None:
    path = tmp_path / "glossary.json"
    path.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "glossary_id": "g.orders",
                        "terms": ["订单"],
                        "target_doc_id": "table.orders",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    entries = load_glossary(path)
    assert entries[0].terms == ("订单",)
    assert entries[0].target_doc_id == "table.orders"
