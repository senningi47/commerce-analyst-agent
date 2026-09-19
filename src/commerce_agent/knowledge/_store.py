"""Internal storage seam for versioned Knowledge retrieval."""

from dataclasses import dataclass
from typing import Protocol

from commerce_agent.knowledge.contracts import KnowledgeEvidence


@dataclass(frozen=True)
class StoredKnowledgeCatalog:
    revision: str
    content_sha256: str
    evidence: tuple[KnowledgeEvidence, ...]


class KnowledgeStore(Protocol):
    async def load_retail_catalog(self) -> StoredKnowledgeCatalog | None: ...
