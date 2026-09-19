from commerce_agent.knowledge.contracts import KnowledgeBundle, KnowledgeKind

KIND_ORDER = {
    KnowledgeKind.TABLE: 0,
    KnowledgeKind.COLUMN: 1,
    KnowledgeKind.JOIN: 2,
    KnowledgeKind.METRIC: 3,
    KnowledgeKind.DATA_QUALITY: 4,
    KnowledgeKind.PERMISSION: 5,
}


def assert_bundle_contract(bundle: KnowledgeBundle) -> None:
    assert bundle.strategy == "full_catalog"
    assert bundle.evidence
    assert len(bundle.catalog_sha256) == 64
    assert all(item.revision == bundle.catalog_revision for item in bundle.evidence)
    assert list(bundle.evidence) == sorted(
        bundle.evidence,
        key=lambda item: (KIND_ORDER[item.kind], item.doc_id),
    )
    gmv = next(item for item in bundle.evidence if item.doc_id == "metric.gmv")
    assert gmv.content["status"] == "clarification_required"
