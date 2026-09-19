import json
import os
import re
from pathlib import Path

import psycopg
import pytest
from pydantic import SecretStr

from commerce_agent.value_resolver._postgres import PostgresValueStore
from commerce_agent.value_resolver.contracts import (
    MatchMethod,
    ResolutionStatus,
    ValueDomain,
    ValueResolutionRequest,
)
from commerce_agent.value_resolver.resolver import BusinessValueResolver
from tests.contracts.value_resolver import assert_result_contract

pytestmark = pytest.mark.postgres


@pytest.fixture
def resolver() -> BusinessValueResolver:
    return BusinessValueResolver(
        PostgresValueStore(SecretStr(os.environ["PRODUCT_DATABASE_DSN"]))
    )


@pytest.mark.asyncio
async def test_normalized_city_and_category_alias_resolve_through_public_interface(
    resolver: BusinessValueResolver,
) -> None:
    city = await resolver.resolve(
        ValueResolutionRequest(domain=ValueDomain.CUSTOMER_CITY, raw_text=" SAO PAULO ")
    )
    category = await resolver.resolve(
        ValueResolutionRequest(
            domain=ValueDomain.PRODUCT_CATEGORY,
            raw_text="HEALTH＿BEAUTY",
        )
    )

    assert_result_contract(city)
    assert city.status is ResolutionStatus.RESOLVED
    assert city.candidates[0].canonical_value == "sao paulo"
    assert category.status is ResolutionStatus.RESOLVED
    assert category.candidates[0].canonical_value == "beleza_saude"
    assert category.candidates[0].match_method is MatchMethod.ALIAS


@pytest.mark.asyncio
async def test_misspelled_city_returns_bounded_deterministic_candidates(
    resolver: BusinessValueResolver,
) -> None:
    first = await resolver.resolve(
        ValueResolutionRequest(domain=ValueDomain.CUSTOMER_CITY, raw_text="sao pauloo")
    )
    second = await resolver.resolve(
        ValueResolutionRequest(domain=ValueDomain.CUSTOMER_CITY, raw_text="sao pauloo")
    )

    assert first.status is ResolutionStatus.AMBIGUOUS
    assert 1 <= len(first.candidates) <= 10
    assert first == second


@pytest.mark.asyncio
async def test_id_prefix_returns_only_hash_shaped_evidence_in_assertions(
    resolver: BusinessValueResolver,
) -> None:
    connection = await psycopg.AsyncConnection.connect(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
    )
    try:
        cursor = await connection.execute(
            "SELECT left(order_id, 8) AS prefix FROM retail.orders "
            "GROUP BY prefix HAVING count(*) <= 10 ORDER BY count(*), prefix LIMIT 1"
        )
        prefix = (await cursor.fetchone())[0]
    finally:
        await connection.close()

    result = await resolver.resolve(
        ValueResolutionRequest(domain=ValueDomain.ORDER_ID, raw_text=prefix)
    )

    assert result.status is ResolutionStatus.AMBIGUOUS
    assert 1 <= len(result.candidates) <= 10
    assert all(
        re.fullmatch(r"value:order_id:[0-9a-f]{16}", item.evidence_ref)
        for item in result.candidates
    )


@pytest.mark.asyncio
async def test_fixed_queries_treat_sql_metacharacters_as_data(
    resolver: BusinessValueResolver,
) -> None:
    result = await resolver.resolve(
        ValueResolutionRequest(
            domain=ValueDomain.CUSTOMER_CITY,
            raw_text="%' OR true --",
        )
    )

    assert result.status in {ResolutionStatus.NOT_FOUND, ResolutionStatus.AMBIGUOUS}
    assert len(result.candidates) <= 10


@pytest.mark.asyncio
async def test_resolver_revision_matches_checked_in_catalog(
    resolver: BusinessValueResolver,
) -> None:
    catalog = json.loads(
        Path("data/knowledge/retail_catalog.v1.json").read_text(encoding="utf-8")
    )

    result = await resolver.resolve(
        ValueResolutionRequest(domain=ValueDomain.ORDER_STATUS, raw_text="delivered")
    )

    assert result.catalog_revision == catalog["revision_id"]
    assert result.data_manifest_sha256 == catalog["data_manifest_sha256"]
