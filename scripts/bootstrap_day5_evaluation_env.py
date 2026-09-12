"""Create or harden the Day 5 evaluation roles without applying database objects."""

import os
from collections.abc import Mapping
from typing import Any

import psycopg
from psycopg import sql

_OWNER_ROLES = ("evaluation_owner",)
_WRITER_ROLES = ("evaluation_writer",)
_NEGATIVE_CAPABILITIES = "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
_PASSWORD_ENVIRONMENT_NAMES = {
    "evaluation_writer": "PRODUCT_EVALUATION_WRITER_PASSWORD",
}


def _role_exists(cursor: psycopg.Cursor[Any], role_name: str) -> bool:
    cursor.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)",
        (role_name,),
    )
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("PostgreSQL did not return a role-existence result")
    return bool(row[0])


def _remove_role_memberships(cursor: psycopg.Cursor[Any]) -> None:
    role_names = (*_OWNER_ROLES, *_WRITER_ROLES)
    cursor.execute(
        "SELECT granted.rolname, member.rolname "
        "FROM pg_auth_members AS membership "
        "JOIN pg_roles AS granted ON granted.oid = membership.roleid "
        "JOIN pg_roles AS member ON member.oid = membership.member "
        "WHERE granted.rolname = ANY(%s) OR member.rolname = ANY(%s)",
        (list(role_names), list(role_names)),
    )
    for granted_role, member_role in cursor.fetchall():
        cursor.execute(
            sql.SQL("REVOKE {} FROM {}").format(
                sql.Identifier(granted_role),
                sql.Identifier(member_role),
            )
        )


def bootstrap_day5_evaluation_roles(admin_dsn: str, passwords: Mapping[str, str]) -> None:
    """Create or harden the reviewed Day 5 owner and application roles."""

    if not admin_dsn:
        raise ValueError("PRODUCT_POSTGRES_ADMIN_DSN must not be empty")
    if set(passwords) != set(_WRITER_ROLES):
        raise ValueError("passwords must contain exactly the reviewed Day 5 writer roles")
    if not all(passwords[role_name] for role_name in _WRITER_ROLES):
        raise ValueError("Day 5 writer passwords must be non-empty")

    with (
        psycopg.connect(admin_dsn, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        for role_name in _OWNER_ROLES:
            action = "ALTER" if _role_exists(cursor, role_name) else "CREATE"
            cursor.execute(
                sql.SQL(
                    f"{action} ROLE {{}} NOLOGIN NOINHERIT {_NEGATIVE_CAPABILITIES}"
                ).format(sql.Identifier(role_name))
            )

        for role_name in _WRITER_ROLES:
            action = "ALTER" if _role_exists(cursor, role_name) else "CREATE"
            cursor.execute(
                sql.SQL(
                    f"{action} ROLE {{}} LOGIN NOINHERIT {_NEGATIVE_CAPABILITIES} "
                    "PASSWORD {}"
                ).format(sql.Identifier(role_name), sql.Literal(passwords[role_name]))
            )
            cursor.execute(
                sql.SQL("ALTER ROLE {} CONNECTION LIMIT 4").format(
                    sql.Identifier(role_name)
                )
            )
            cursor.execute(
                sql.SQL("ALTER ROLE {} SET temp_file_limit = '64MB'").format(
                    sql.Identifier(role_name)
                )
            )

        _remove_role_memberships(cursor)
        cursor.execute("GRANT CONNECT ON DATABASE commerce_analyst TO evaluation_writer")
        cursor.execute("REVOKE CREATE ON DATABASE commerce_analyst FROM evaluation_writer")
        cursor.execute("REVOKE TEMPORARY ON DATABASE commerce_analyst FROM evaluation_writer")


def main() -> None:
    """Bootstrap the reviewed roles from explicit environment values."""

    bootstrap_day5_evaluation_roles(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"],
        {
            role_name: os.environ[environment_name]
            for role_name, environment_name in _PASSWORD_ENVIRONMENT_NAMES.items()
        },
    )
    print("Day 5 evaluation roles bootstrapped")


if __name__ == "__main__":
    main()
