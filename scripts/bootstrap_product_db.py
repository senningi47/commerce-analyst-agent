"""Create product database roles and global privilege boundaries."""

import os
from typing import Any

import psycopg
from psycopg import sql


def _role_exists(cursor: psycopg.Cursor[Any], role_name: str) -> bool:
    cursor.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)",
        (role_name,),
    )
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("PostgreSQL did not return a role-existence result")
    return bool(row[0])


def bootstrap_roles(
    admin_dsn: str,
    reader_password: str,
    knowledge_reader_password: str,
) -> None:
    """Create or harden the product owner and query roles."""

    if not admin_dsn:
        raise ValueError("PRODUCT_POSTGRES_ADMIN_DSN must not be empty")
    if not reader_password:
        raise ValueError("PRODUCT_AGENT_READER_PASSWORD must not be empty")
    if not knowledge_reader_password:
        raise ValueError("PRODUCT_KNOWLEDGE_READER_PASSWORD must not be empty")
    if reader_password == knowledge_reader_password:
        raise ValueError("agent and knowledge reader passwords must differ")

    with (
        psycopg.connect(admin_dsn, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        for owner_role in ("retail_owner", "knowledge_owner"):
            identifier = sql.Identifier(owner_role)
            if _role_exists(cursor, owner_role):
                cursor.execute(
                    sql.SQL(
                        "ALTER ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                    ).format(identifier)
                )
            else:
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                    ).format(identifier)
                )

        password_literal = sql.Literal(reader_password)
        if _role_exists(cursor, "agent_reader"):
            cursor.execute(
                sql.SQL(
                    "ALTER ROLE agent_reader LOGIN NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(password_literal)
            )
        else:
            cursor.execute(
                sql.SQL(
                    "CREATE ROLE agent_reader LOGIN NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(password_literal)
            )

        knowledge_password_literal = sql.Literal(knowledge_reader_password)
        if _role_exists(cursor, "knowledge_reader"):
            cursor.execute(
                sql.SQL(
                    "ALTER ROLE knowledge_reader LOGIN NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(knowledge_password_literal)
            )
        else:
            cursor.execute(
                sql.SQL(
                    "CREATE ROLE knowledge_reader LOGIN NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(knowledge_password_literal)
            )

        cursor.execute(
            "REVOKE CONNECT, TEMPORARY ON DATABASE commerce_analyst FROM PUBLIC"
        )
        cursor.execute(
            "GRANT CONNECT ON DATABASE commerce_analyst TO "
            "commerce_admin, agent_reader, knowledge_reader"
        )
        cursor.execute(
            "REVOKE TEMPORARY ON DATABASE commerce_analyst FROM agent_reader, knowledge_reader"
        )
        cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        cursor.execute("ALTER ROLE agent_reader SET default_transaction_read_only = on")
        cursor.execute("ALTER ROLE agent_reader SET temp_file_limit = '64MB'")
        cursor.execute("ALTER ROLE agent_reader CONNECTION LIMIT 4")
        cursor.execute("ALTER ROLE knowledge_reader SET default_transaction_read_only = on")
        cursor.execute("ALTER ROLE knowledge_reader SET temp_file_limit = '64MB'")
        cursor.execute("ALTER ROLE knowledge_reader CONNECTION LIMIT 4")


def main() -> None:
    """Read secrets at the process boundary and run the bootstrap."""

    bootstrap_roles(
        admin_dsn=os.environ["PRODUCT_POSTGRES_ADMIN_DSN"],
        reader_password=os.environ["PRODUCT_AGENT_READER_PASSWORD"],
        knowledge_reader_password=os.environ["PRODUCT_KNOWLEDGE_READER_PASSWORD"],
    )
    print("Product database roles bootstrapped")


if __name__ == "__main__":
    main()
