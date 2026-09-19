import pytest
from pydantic import ValidationError

from commerce_agent.knowledge.contracts import (
    KnowledgeBundle,
    KnowledgeEvidence,
    KnowledgeKind,
    KnowledgeRequest,
)
from commerce_agent.knowledge.errors import KnowledgeRevisionUnavailable

SOURCE_SHA256 = "c" * 64
CATALOG_SHA256 = "d" * 64


def evidence() -> KnowledgeEvidence:
    return KnowledgeEvidence(
        doc_id="metric.gmv",
        revision="retail-catalog-v1",
        kind=KnowledgeKind.METRIC,
        title="GMV",
        content={"status": "clarification_required"},
        source_type="design_spec",
        source_path="docs/project/specs/day-2b.md",
        source_sha256=SOURCE_SHA256,
    )


def test_knowledge_request_accepts_only_retail_profile() -> None:
    request = KnowledgeRequest(question="What is item_amount?")

    assert request.profile == "retail"
    with pytest.raises(ValidationError):
        KnowledgeRequest(question="schema", profile="bird_a")


@pytest.mark.parametrize("question", ["", " " * 4_001])
def test_knowledge_request_rejects_invalid_question(question: str) -> None:
    with pytest.raises(ValidationError):
        KnowledgeRequest(question=question)


def test_knowledge_kind_is_closed() -> None:
    assert {kind.value for kind in KnowledgeKind} == {
        "table",
        "column",
        "join",
        "metric",
        "data_quality",
        "permission",
    }


def test_bundle_is_immutable_and_requires_evidence() -> None:
    bundle = KnowledgeBundle(
        catalog_revision="retail-catalog-v1",
        catalog_sha256=CATALOG_SHA256,
        evidence=(evidence(),),
    )

    assert bundle.strategy == "full_catalog"
    with pytest.raises(ValidationError):
        bundle.evidence = ()
    with pytest.raises(ValidationError):
        KnowledgeBundle(
            catalog_revision="retail-catalog-v1",
            catalog_sha256=CATALOG_SHA256,
            evidence=(),
        )


def test_evidence_rejects_unknown_kind_and_invalid_hash() -> None:
    with pytest.raises(ValidationError):
        KnowledgeEvidence(
            doc_id="metric.gmv",
            revision="retail-catalog-v1",
            kind="instruction",
            title="GMV",
            content={},
            source_type="design_spec",
            source_path="spec.md",
            source_sha256="not-a-hash",
        )


def test_knowledge_error_exposes_only_stable_reason_and_safe_message() -> None:
    error = KnowledgeRevisionUnavailable("revision_missing", "no active revision")

    assert error.reason_code == "revision_missing"
    assert str(error) == "no active revision"
