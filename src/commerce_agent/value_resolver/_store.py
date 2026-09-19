"""Internal storage seam for business-value resolution."""

from dataclasses import dataclass
from typing import Literal, Protocol

from commerce_agent.value_resolver.contracts import ValueDomain


@dataclass(frozen=True)
class StoredRevision:
    data_manifest_sha256: str
    catalog_revision: str
    normalization_revision: Literal["value-normalization-v1"] = "value-normalization-v1"


@dataclass(frozen=True)
class StoredValue:
    canonical_value: str
    display_label: str
    support_count: int


@dataclass(frozen=True)
class StoredAlias:
    normalized_alias: str
    canonical_value: str
    display_label: str


@dataclass(frozen=True)
class TextDomainSnapshot:
    values: tuple[StoredValue, ...]
    aliases: tuple[StoredAlias, ...]
    revision: StoredRevision


@dataclass(frozen=True)
class IdLookup:
    values: tuple[StoredValue, ...]
    exceeded_limit: bool
    revision: StoredRevision


class ValueStore(Protocol):
    async def load_revision(self) -> StoredRevision: ...

    async def load_text_domain(self, domain: ValueDomain) -> TextDomainSnapshot: ...

    async def lookup_id(
        self,
        domain: ValueDomain,
        value: str,
        *,
        prefix: bool,
        limit: int,
    ) -> IdLookup: ...
