import pytest

from commerce_agent.knowledge._store import StoredKnowledgeCatalog
from commerce_agent.knowledge.contracts import (
    KnowledgeEvidence,
    KnowledgeKind,
    KnowledgeRequest,
)
from commerce_agent.knowledge.errors import (
    KnowledgeCatalogInvalid,
    KnowledgeRevisionUnavailable,
)
from commerce_agent.knowledge.module import KnowledgeModule
from tests.contracts.knowledge import assert_bundle_contract

CATALOG_SHA256 = "b" * 64
SOURCE_SHA256 = "c" * 64


def evidence(
    doc_id: str,
    kind: KnowledgeKind,
    *,
    content: dict[str, object] | None = None,
    revision: str = "retail-catalog-v1",
) -> KnowledgeEvidence:
    return KnowledgeEvidence(
        doc_id=doc_id,
        revision=revision,
        kind=kind,
        title=doc_id,
        content=content or {"status": "active"},
        source_type="test_fixture",
        source_path="tests/unit/knowledge/test_module.py",
        source_sha256=SOURCE_SHA256,
    )


class InMemoryKnowledgeStore:
    def __init__(self, catalog: StoredKnowledgeCatalog | None) -> None:
        self.catalog = catalog

    async def load_retail_catalog(self) -> StoredKnowledgeCatalog | None:
        return self.catalog


@pytest.mark.asyncio
async def test_full_catalog_is_stably_sorted() -> None:
    stored = StoredKnowledgeCatalog(
        revision="retail-catalog-v1",
        content_sha256=CATALOG_SHA256,
        evidence=(
            evidence("permission.agent_reader", KnowledgeKind.PERMISSION),
            evidence(
                "metric.gmv",
                KnowledgeKind.METRIC,
                content={"status": "clarification_required"},
            ),
            evidence("table.orders", KnowledgeKind.TABLE),
            evidence("join.orders_customers", KnowledgeKind.JOIN),
        ),
    )
    module = KnowledgeModule(InMemoryKnowledgeStore(stored))

    bundle = await module.retrieve(KnowledgeRequest(question="GMV by state"))

    assert_bundle_contract(bundle)
    assert [item.doc_id for item in bundle.evidence] == [
        "table.orders",
        "join.orders_customers",
        "metric.gmv",
        "permission.agent_reader",
    ]


@pytest.mark.asyncio
async def test_missing_active_revision_fails_closed() -> None:
    module = KnowledgeModule(InMemoryKnowledgeStore(None))

    with pytest.raises(KnowledgeRevisionUnavailable) as caught:
        await module.retrieve(KnowledgeRequest(question="schema"))

    assert caught.value.reason_code == "revision_missing"


@pytest.mark.asyncio
async def test_empty_evidence_fails_closed() -> None:
    module = KnowledgeModule(
        InMemoryKnowledgeStore(
            StoredKnowledgeCatalog(
                revision="retail-catalog-v1",
                content_sha256=CATALOG_SHA256,
                evidence=(),
            )
        )
    )

    with pytest.raises(KnowledgeCatalogInvalid) as caught:
        await module.retrieve(KnowledgeRequest(question="schema"))

    assert caught.value.reason_code == "evidence_empty"


@pytest.mark.asyncio
async def test_mismatched_revision_and_missing_gmv_fail_closed() -> None:
    mismatched = KnowledgeModule(
        InMemoryKnowledgeStore(
            StoredKnowledgeCatalog(
                revision="retail-catalog-v1",
                content_sha256=CATALOG_SHA256,
                evidence=(evidence("table.orders", KnowledgeKind.TABLE, revision="v2"),),
            )
        )
    )
    without_gmv = KnowledgeModule(
        InMemoryKnowledgeStore(
            StoredKnowledgeCatalog(
                revision="retail-catalog-v1",
                content_sha256=CATALOG_SHA256,
                evidence=(evidence("table.orders", KnowledgeKind.TABLE),),
            )
        )
    )

    with pytest.raises(KnowledgeCatalogInvalid, match="revision"):
        await mismatched.retrieve(KnowledgeRequest(question="schema"))
    with pytest.raises(KnowledgeCatalogInvalid) as caught:
        await without_gmv.retrieve(KnowledgeRequest(question="metrics"))

    assert caught.value.reason_code == "gmv_missing"
