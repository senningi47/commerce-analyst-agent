"""Public immutable contracts for versioned product Knowledge."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, JsonValue


class KnowledgeKind(StrEnum):
    TABLE = "table"
    COLUMN = "column"
    JOIN = "join"
    METRIC = "metric"
    DATA_QUALITY = "data_quality"
    PERMISSION = "permission"


class KnowledgeRequest(BaseModel, frozen=True):
    question: str = Field(min_length=1, max_length=4_000)
    profile: Literal["retail"] = "retail"


class KnowledgeEvidence(BaseModel, frozen=True):
    doc_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")
    revision: str = Field(min_length=1, max_length=128)
    kind: KnowledgeKind
    title: str = Field(min_length=1, max_length=200)
    content: dict[str, JsonValue]
    source_type: str = Field(min_length=1, max_length=64)
    source_path: str = Field(min_length=1, max_length=500)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class KnowledgeBundle(BaseModel, frozen=True):
    catalog_revision: str = Field(min_length=1, max_length=128)
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy: Literal["full_catalog"] = "full_catalog"
    evidence: tuple[KnowledgeEvidence, ...] = Field(min_length=1)
