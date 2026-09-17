"""§10.2 route-2 metadata retrieval: BM25 over the knowledge catalog.

Falsifiable-ladder route 2 for the §17.1 condition-B context. The tokenizer
revision is pinned (`bm25-tokenizer-v1`): Unicode NFKC lowercasing, latin
word tokens, CJK single characters. The corpus is the knowledge catalog
augmented with the bilingual glossary (§10.2 anticipates a mixed zh/latin
corpus; the glossary bridges operator vocabulary onto catalog documents and
is applied identically to both §17.1 conditions). A question retrieves at
most the top-5 documents whose score reaches the preregistered relative
threshold — the §17.1 condition-B gate, frozen here. Gold Recall@5 compares
the retrieved table documents against a question's gold tables.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

from commerce_agent.knowledge.contracts import KnowledgeEvidence

TOKENIZER_REVISION = "bm25-tokenizer-v1"
RETRIEVAL_TOP_K = 5
RETRIEVAL_MIN_RELATIVE_SCORE = 0.3


@dataclass(frozen=True)
class GlossaryEntry:
    glossary_id: str
    terms: tuple[str, ...]
    target_doc_id: str


@dataclass(frozen=True)
class CorpusDoc:
    doc_id: str
    text: str
    table_name: str | None


@dataclass(frozen=True)
class RetrievalHit:
    doc: CorpusDoc
    score: float
    rank: int


def load_glossary(path: Path) -> tuple[GlossaryEntry, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        GlossaryEntry(
            glossary_id=item["glossary_id"],
            terms=tuple(item["terms"]),
            target_doc_id=item["target_doc_id"],
        )
        for item in payload["entries"]
    )


def tokenize(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    tokens: list[str] = []
    current: list[str] = []
    for char in normalized:
        if char.isascii() and (char.isalnum() or char == "_"):
            current.append(char)
            continue
        if current:
            tokens.append("".join(current))
            current = []
        if "\u4e00" <= char <= "\u9fff":
            tokens.append(char)
    if current:
        tokens.append("".join(current))
    return tokens


def _evidence_text(evidence: KnowledgeEvidence) -> str:
    return " ".join(
        (
            evidence.doc_id.replace("_", " ").replace(".", " "),
            evidence.title,
            json.dumps(evidence.content, ensure_ascii=False, sort_keys=True),
        )
    )


def build_corpus(
    evidence: tuple[KnowledgeEvidence, ...],
    glossary: tuple[GlossaryEntry, ...] = (),
) -> tuple[CorpusDoc, ...]:
    """One corpus doc per catalog document, its indexed text augmented with
    the glossary terms that resolve to it (identically for both §17.1
    conditions, so the A/B isolates retrieval compression)."""
    by_target: dict[str, list[str]] = {}
    for entry in glossary:
        by_target.setdefault(entry.target_doc_id, []).extend(entry.terms)
    docs: list[CorpusDoc] = []
    for item in evidence:
        terms = by_target.get(item.doc_id, [])
        text = _evidence_text(item)
        if terms:
            text = f"{text} {' '.join(terms)}"
        table_name = item.doc_id.removeprefix("table.")
        docs.append(
            CorpusDoc(
                doc_id=item.doc_id,
                text=text,
                table_name=table_name if item.doc_id.startswith("table.") else None,
            )
        )
    return tuple(docs)


def retrieve_metadata(
    question: str, corpus: tuple[CorpusDoc, ...]
) -> tuple[RetrievalHit, ...]:
    """Score every corpus doc against the question and return the
    thresholded top-5 in descending BM25 score (deterministic order)."""
    if not corpus:
        return ()
    engine = BM25Okapi([tokenize(doc.text) for doc in corpus])
    scores = engine.get_scores(tokenize(question))
    ranked = sorted(
        zip(corpus, scores, strict=True), key=lambda pair: (-pair[1], pair[0].doc_id)
    )
    best = float(ranked[0][1])
    floor = best * RETRIEVAL_MIN_RELATIVE_SCORE
    hits = [
        RetrievalHit(doc=doc, score=float(score), rank=rank + 1)
        for rank, (doc, score) in enumerate(ranked)
        if rank < RETRIEVAL_TOP_K and float(score) >= floor and float(score) > 0.0
    ]
    return tuple(hits)


def gold_table_recall(
    hits: tuple[RetrievalHit, ...], gold_tables: tuple[str, ...] | list[str]
) -> float:
    """Fraction of gold tables covered by the retrieved table documents."""
    if not gold_tables:
        return 1.0
    covered = {hit.doc.table_name for hit in hits if hit.doc.table_name is not None}
    return sum(1 for table in gold_tables if table in covered) / len(gold_tables)


def full_catalog_table_recall(
    corpus: tuple[CorpusDoc, ...], gold_tables: tuple[str, ...] | list[str]
) -> float:
    """Condition-A recall: the full corpus carries every table document."""
    if not gold_tables:
        return 1.0
    available = {doc.table_name for doc in corpus if doc.table_name is not None}
    return sum(1 for table in gold_tables if table in available) / len(gold_tables)
