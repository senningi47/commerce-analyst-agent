"""PostgreSQL ValueStore using only closed fixed-query templates."""

from typing import Any

import psycopg
from pydantic import SecretStr

from commerce_agent.value_resolver._store import (
    IdLookup,
    StoredAlias,
    StoredRevision,
    StoredValue,
    TextDomainSnapshot,
)
from commerce_agent.value_resolver.contracts import ValueDomain
from commerce_agent.value_resolver.errors import (
    ValueDomainUnavailable,
    ValueResolutionDataError,
    ValueResolutionInfrastructureError,
    ValueResolverError,
)

_SET_LOCAL_STATEMENTS = (
    "SET LOCAL statement_timeout = '5s'",
    "SET LOCAL lock_timeout = '1s'",
    "SET LOCAL idle_in_transaction_session_timeout = '10s'",
    "SET LOCAL work_mem = '16MB'",
    "SET LOCAL max_parallel_workers_per_gather = 0",
    "SET LOCAL search_path = retail, ops_read",
)

_TEXT_QUERIES = {
    ValueDomain.CUSTOMER_STATE: (
        "SELECT customer_state, count(*) FROM retail.customers "
        "GROUP BY customer_state ORDER BY customer_state LIMIT 10001"
    ),
    ValueDomain.SELLER_STATE: (
        "SELECT seller_state, count(*) FROM retail.sellers "
        "GROUP BY seller_state ORDER BY seller_state LIMIT 10001"
    ),
    ValueDomain.ORDER_STATUS: (
        "SELECT order_status, count(*) FROM retail.orders "
        "GROUP BY order_status ORDER BY order_status LIMIT 10001"
    ),
    ValueDomain.PAYMENT_TYPE: (
        "SELECT payment_type, count(*) FROM retail.order_payments "
        "GROUP BY payment_type ORDER BY payment_type LIMIT 10001"
    ),
    ValueDomain.PRODUCT_CATEGORY: (
        "SELECT product_category_name, count(*) FROM retail.products "
        "WHERE product_category_name IS NOT NULL GROUP BY product_category_name "
        "ORDER BY product_category_name LIMIT 10001"
    ),
    ValueDomain.CUSTOMER_CITY: (
        "SELECT customer_city, count(*) FROM retail.customers "
        "GROUP BY customer_city ORDER BY customer_city LIMIT 10001"
    ),
    ValueDomain.SELLER_CITY: (
        "SELECT seller_city, count(*) FROM retail.sellers "
        "GROUP BY seller_city ORDER BY seller_city LIMIT 10001"
    ),
}

_ID_QUERIES = {
    ValueDomain.SELLER_ID: {
        False: (
            "SELECT seller_id FROM retail.sellers WHERE seller_id = %s ORDER BY seller_id LIMIT %s"
        ),
        True: (
            "SELECT seller_id FROM retail.sellers WHERE seller_id LIKE %s "
            "ORDER BY seller_id LIMIT %s"
        ),
    },
    ValueDomain.ORDER_ID: {
        False: (
            "SELECT order_id FROM retail.orders WHERE order_id = %s ORDER BY order_id LIMIT %s"
        ),
        True: (
            "SELECT order_id FROM retail.orders WHERE order_id LIKE %s ORDER BY order_id LIMIT %s"
        ),
    },
}


async def _begin_guarded_transaction(connection: psycopg.AsyncConnection[Any]) -> None:
    await connection.execute("BEGIN READ ONLY")
    for statement in _SET_LOCAL_STATEMENTS:
        await connection.execute(statement)


async def _read_revision(connection: psycopg.AsyncConnection[Any]) -> StoredRevision:
    cursor = await connection.execute(
        "SELECT DISTINCT data_manifest_sha256, revision "
        "FROM ops_read.business_value_aliases ORDER BY revision"
    )
    rows = await cursor.fetchall()
    if len(rows) != 1:
        raise ValueDomainUnavailable(
            "revision_unavailable",
            "one active Resolver catalog revision is required",
        )
    return StoredRevision(
        data_manifest_sha256=str(rows[0][0]),
        catalog_revision=str(rows[0][1]),
    )


class PostgresValueStore:
    def __init__(self, dsn: SecretStr) -> None:
        self._dsn = dsn

    async def _connect(self) -> psycopg.AsyncConnection[Any]:
        try:
            return await psycopg.AsyncConnection.connect(
                self._dsn.get_secret_value(),
                application_name="commerce_value_resolver",
                connect_timeout=3,
            )
        except psycopg.Error as error:
            raise ValueResolutionInfrastructureError(
                "connection_failed",
                "Product database connection failed",
            ) from error

    async def load_revision(self) -> StoredRevision:
        connection = await self._connect()
        try:
            await _begin_guarded_transaction(connection)
            return await _read_revision(connection)
        except ValueResolverError:
            raise
        except psycopg.Error as error:
            raise ValueResolutionInfrastructureError(
                "postgres_error",
                "PostgreSQL rejected the Resolver query",
            ) from error
        finally:
            try:
                await connection.rollback()
            finally:
                await connection.close()

    async def load_text_domain(self, domain: ValueDomain) -> TextDomainSnapshot:
        statement = _TEXT_QUERIES.get(domain)
        if statement is None:
            raise ValueDomainUnavailable(
                "domain_unavailable",
                "the requested text domain is unavailable",
            )
        connection = await self._connect()
        try:
            await _begin_guarded_transaction(connection)
            revision = await _read_revision(connection)
            cursor = await connection.execute(statement)
            rows = await cursor.fetchall()
            if len(rows) > 10_000:
                raise ValueResolutionDataError(
                    "domain_cardinality",
                    "value domain exceeds the reviewed cardinality limit",
                )
            values = tuple(
                StoredValue(
                    canonical_value=str(row[0]),
                    display_label=str(row[0]),
                    support_count=int(row[1]),
                )
                for row in rows
            )
            alias_cursor = await connection.execute(
                "SELECT normalized_alias, canonical_value, display_label "
                "FROM ops_read.business_value_aliases WHERE domain = %s "
                "ORDER BY normalized_alias, canonical_value",
                (domain.value,),
            )
            aliases = tuple(
                StoredAlias(str(row[0]), str(row[1]), str(row[2]))
                for row in await alias_cursor.fetchall()
            )
            return TextDomainSnapshot(values=values, aliases=aliases, revision=revision)
        except ValueResolverError:
            raise
        except (IndexError, TypeError, ValueError) as error:
            raise ValueResolutionDataError(
                "result_shape",
                "Resolver query returned an invalid result shape",
            ) from error
        except psycopg.Error as error:
            raise ValueResolutionInfrastructureError(
                "postgres_error",
                "PostgreSQL rejected the Resolver query",
            ) from error
        finally:
            try:
                await connection.rollback()
            finally:
                await connection.close()

    async def lookup_id(
        self,
        domain: ValueDomain,
        value: str,
        *,
        prefix: bool,
        limit: int,
    ) -> IdLookup:
        statements = _ID_QUERIES.get(domain)
        if statements is None:
            raise ValueDomainUnavailable(
                "domain_unavailable",
                "the requested ID domain is unavailable",
            )
        connection = await self._connect()
        try:
            await _begin_guarded_transaction(connection)
            revision = await _read_revision(connection)
            query_value = value + "%" if prefix else value
            cursor = await connection.execute(
                statements[prefix],
                (query_value, limit),
            )
            rows = await cursor.fetchall()
            values = tuple(StoredValue(str(row[0]), str(row[0]), 1) for row in rows)
            return IdLookup(
                values=values,
                exceeded_limit=len(values) >= limit,
                revision=revision,
            )
        except ValueResolverError:
            raise
        except (IndexError, TypeError, ValueError) as error:
            raise ValueResolutionDataError(
                "result_shape",
                "Resolver query returned an invalid result shape",
            ) from error
        except psycopg.Error as error:
            raise ValueResolutionInfrastructureError(
                "postgres_error",
                "PostgreSQL rejected the Resolver query",
            ) from error
        finally:
            try:
                await connection.rollback()
            finally:
                await connection.close()
