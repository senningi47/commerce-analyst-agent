"""Single public interface for fail-closed Knowledge retrieval."""

from commerce_agent.knowledge._store import KnowledgeStore
from commerce_agent.knowledge.contracts import (
    KnowledgeBundle,
    KnowledgeKind,
    KnowledgeRequest,
)
from commerce_agent.knowledge.errors import (
    KnowledgeCatalogInvalid,
    KnowledgeRevisionUnavailable,
)

_KIND_ORDER = {
    KnowledgeKind.TABLE: 0,
    KnowledgeKind.COLUMN: 1,
    KnowledgeKind.JOIN: 2,
    KnowledgeKind.METRIC: 3,
    KnowledgeKind.DATA_QUALITY: 4,
    KnowledgeKind.PERMISSION: 5,
}


class KnowledgeModule:
    def __init__(self, store: KnowledgeStore) -> None:
        self._store = store

    async def retrieve(self, request: KnowledgeRequest) -> KnowledgeBundle:
        del request
        stored = await self._store.load_retail_catalog()
        if stored is None:
            raise KnowledgeRevisionUnavailable(
                "revision_missing",
                "no active Knowledge revision is available",
            )
        if not stored.evidence:
            raise KnowledgeCatalogInvalid(
                "evidence_empty",
                "the active Knowledge revision contains no retail evidence",
            )
        if any(item.revision != stored.revision for item in stored.evidence):
            raise KnowledgeCatalogInvalid(
                "revision_mismatch",
                "Knowledge evidence does not match the active revision",
            )

        gmv_documents = [item for item in stored.evidence if item.doc_id == "metric.gmv"]
        if not gmv_documents:
            raise KnowledgeCatalogInvalid(
                "gmv_missing",
                "the active Knowledge revision does not define metric.gmv",
            )
        if len(gmv_documents) != 1 or gmv_documents[0].content.get("status") != (
            "clarification_required"
        ):
            raise KnowledgeCatalogInvalid(
                "gmv_definition",
                "metric.gmv must require clarification",
            )

        ordered = tuple(
            sorted(stored.evidence, key=lambda item: (_KIND_ORDER[item.kind], item.doc_id))
        )
        return KnowledgeBundle(
            catalog_revision=stored.revision,
            catalog_sha256=stored.content_sha256,
            evidence=ordered,
        )
