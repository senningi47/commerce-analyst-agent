from collections.abc import Mapping

import pytest

from commerce_agent.value_resolver._store import (
    IdLookup,
    StoredAlias,
    StoredRevision,
    StoredValue,
    TextDomainSnapshot,
)
from commerce_agent.value_resolver.contracts import (
    Confidence,
    MatchMethod,
    ResolutionStatus,
    ValueDomain,
    ValueResolutionRequest,
)
from commerce_agent.value_resolver.errors import ValueResolutionDataError
from commerce_agent.value_resolver.resolver import BusinessValueResolver
from tests.contracts.value_resolver import assert_result_contract

TEST_REVISION = StoredRevision(
    data_manifest_sha256="a" * 64,
    catalog_revision="retail-catalog-v1",
)


class InMemoryValueStore:
    def __init__(
        self,
        *,
        text: Mapping[ValueDomain, tuple[StoredValue, ...]] | None = None,
        aliases: Mapping[ValueDomain, tuple[StoredAlias, ...]] | None = None,
        ids: Mapping[ValueDomain, tuple[str, ...]] | None = None,
    ) -> None:
        self.text = text or {}
        self.aliases = aliases or {}
        self.ids = ids or {}
        self.id_calls = 0

    async def load_revision(self) -> StoredRevision:
        return TEST_REVISION

    async def load_text_domain(self, domain: ValueDomain) -> TextDomainSnapshot:
        return TextDomainSnapshot(
            values=self.text.get(domain, ()),
            aliases=self.aliases.get(domain, ()),
            revision=TEST_REVISION,
        )

    async def lookup_id(
        self,
        domain: ValueDomain,
        value: str,
        *,
        prefix: bool,
        limit: int,
    ) -> IdLookup:
        self.id_calls += 1
        all_values = self.ids.get(domain, ())
        matches = tuple(
            StoredValue(item, item, 1)
            for item in all_values
            if (item.startswith(value) if prefix else item == value)
        )
        return IdLookup(
            values=matches[:limit],
            exceeded_limit=len(matches) >= limit,
            revision=TEST_REVISION,
        )


def value(canonical: str, support_count: int = 1) -> StoredValue:
    return StoredValue(canonical, canonical, support_count)


@pytest.mark.asyncio
async def test_city_normalized_exact_returns_original_canonical_value() -> None:
    resolver = BusinessValueResolver(
        InMemoryValueStore(
            text={ValueDomain.CUSTOMER_CITY: (value("são paulo", 15),)},
        )
    )

    result = await resolver.resolve(
        ValueResolutionRequest(domain=ValueDomain.CUSTOMER_CITY, raw_text=" Sao Paulo ")
    )

    assert_result_contract(result)
    assert result.status is ResolutionStatus.RESOLVED
    assert result.candidates[0].canonical_value == "são paulo"
    assert result.candidates[0].match_method is MatchMethod.NORMALIZED_EXACT


@pytest.mark.asyncio
async def test_reviewed_alias_resolves_to_canonical_value() -> None:
    resolver = BusinessValueResolver(
        InMemoryValueStore(
            text={ValueDomain.PRODUCT_CATEGORY: (value("beleza_saude", 8),)},
            aliases={
                ValueDomain.PRODUCT_CATEGORY: (
                    StoredAlias("health_beauty", "beleza_saude", "Beleza e saúde"),
                )
            },
        )
    )

    result = await resolver.resolve(
        ValueResolutionRequest(
            domain=ValueDomain.PRODUCT_CATEGORY,
            raw_text="HEALTH＿BEAUTY",
        )
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.candidates[0].match_method is MatchMethod.ALIAS
    assert result.candidates[0].canonical_value == "beleza_saude"


@pytest.mark.asyncio
async def test_normalization_collision_is_ambiguous_and_deterministic() -> None:
    resolver = BusinessValueResolver(
        InMemoryValueStore(
            text={
                ValueDomain.CUSTOMER_CITY: (
                    value("são paulo", 20),
                    value("sao paulo", 10),
                )
            }
        )
    )

    result = await resolver.resolve(
        ValueResolutionRequest(domain=ValueDomain.CUSTOMER_CITY, raw_text="SAO PAULO")
    )

    assert result.status is ResolutionStatus.AMBIGUOUS
    assert [item.canonical_value for item in result.candidates] == [
        "são paulo",
        "sao paulo",
    ]


@pytest.mark.asyncio
async def test_trigram_candidates_are_never_auto_resolved_and_are_capped() -> None:
    values = tuple(value(f"sao paul{suffix}", 20 - suffix) for suffix in range(12))
    resolver = BusinessValueResolver(InMemoryValueStore(text={ValueDomain.CUSTOMER_CITY: values}))

    result = await resolver.resolve(
        ValueResolutionRequest(domain=ValueDomain.CUSTOMER_CITY, raw_text="sao paulx")
    )

    assert result.status is ResolutionStatus.AMBIGUOUS
    assert 1 <= len(result.candidates) <= 10
    assert all(item.match_method is MatchMethod.TRIGRAM for item in result.candidates)


@pytest.mark.asyncio
async def test_text_no_match_returns_not_found() -> None:
    resolver = BusinessValueResolver(
        InMemoryValueStore(text={ValueDomain.ORDER_STATUS: (value("delivered"),)})
    )

    result = await resolver.resolve(
        ValueResolutionRequest(domain=ValueDomain.ORDER_STATUS, raw_text="zzzzzzzz")
    )

    assert_result_contract(result)
    assert result.status is ResolutionStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_single_prefix_candidate_still_requires_confirmation() -> None:
    canonical_id = "a1b2c3d4" + "0" * 24
    resolver = BusinessValueResolver(
        InMemoryValueStore(ids={ValueDomain.SELLER_ID: (canonical_id,)})
    )

    result = await resolver.resolve(
        ValueResolutionRequest(domain=ValueDomain.SELLER_ID, raw_text="A1B2C3")
    )

    assert result.status is ResolutionStatus.AMBIGUOUS
    assert result.candidates[0].match_method is MatchMethod.PREFIX
    assert result.candidates[0].confidence is Confidence.HIGH


@pytest.mark.asyncio
async def test_full_id_exact_match_is_resolved() -> None:
    canonical_id = "a" * 32
    resolver = BusinessValueResolver(
        InMemoryValueStore(ids={ValueDomain.ORDER_ID: (canonical_id,)})
    )

    result = await resolver.resolve(
        ValueResolutionRequest(domain=ValueDomain.ORDER_ID, raw_text=canonical_id)
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.candidates[0].match_method is MatchMethod.EXACT


@pytest.mark.asyncio
async def test_short_id_is_too_broad_without_querying_store() -> None:
    store = InMemoryValueStore(ids={ValueDomain.ORDER_ID: ("a" * 32,)})
    resolver = BusinessValueResolver(store)

    result = await resolver.resolve(
        ValueResolutionRequest(domain=ValueDomain.ORDER_ID, raw_text="abcde")
    )

    assert result.status is ResolutionStatus.TOO_BROAD
    assert store.id_calls == 0


@pytest.mark.asyncio
async def test_more_than_ten_id_candidates_is_too_broad() -> None:
    ids = tuple(f"abcdef{number:026x}" for number in range(11))
    resolver = BusinessValueResolver(InMemoryValueStore(ids={ValueDomain.ORDER_ID: ids}))

    result = await resolver.resolve(
        ValueResolutionRequest(domain=ValueDomain.ORDER_ID, raw_text="abcdef")
    )

    assert result.status is ResolutionStatus.TOO_BROAD
    assert result.candidates == ()


@pytest.mark.asyncio
async def test_domain_cardinality_above_limit_fails_closed() -> None:
    values = tuple(value(f"city-{number}") for number in range(10_001))
    resolver = BusinessValueResolver(InMemoryValueStore(text={ValueDomain.CUSTOMER_CITY: values}))

    with pytest.raises(ValueResolutionDataError) as caught:
        await resolver.resolve(
            ValueResolutionRequest(domain=ValueDomain.CUSTOMER_CITY, raw_text="city")
        )

    assert caught.value.reason_code == "domain_cardinality"
