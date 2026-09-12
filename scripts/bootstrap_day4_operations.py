"""Create or harden Day 4 operation roles without applying database objects."""

import os
from collections.abc import Mapping
from typing import Any

import psycopg
from psycopg import sql

_OWNER_ROLES = ("ops_owner", "audit_owner", "scenario_reset_owner")
_WRITER_ROLES = (
    "proposal_writer",
    "approval_writer",
    "operation_executor",
    "trace_writer",
    "product_scenario_reset",
)
_NEGATIVE_CAPABILITIES = (
    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
)
_PASSWORD_ENVIRONMENT_NAMES = {
    "proposal_writer": "PRODUCT_PROPOSAL_WRITER_PASSWORD",
    "approval_writer": "PRODUCT_APPROVAL_WRITER_PASSWORD",
    "operation_executor": "PRODUCT_OPERATION_EXECUTOR_PASSWORD",
    "trace_writer": "PRODUCT_TRACE_WRITER_PASSWORD",
    "product_scenario_reset": "PRODUCT_SCENARIO_RESET_PASSWORD",
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


def bootstrap_day4_roles(admin_dsn: str, passwords: Mapping[str, str]) -> None:
    """Create or harden the reviewed Day 4 owner and application roles."""

    if not admin_dsn:
        raise ValueError("PRODUCT_POSTGRES_ADMIN_DSN must not be empty")
    if set(passwords) != set(_WRITER_ROLES):
        raise ValueError("passwords must contain exactly the reviewed Day 4 writer roles")
    ordered_passwords = [passwords[role_name] for role_name in _WRITER_ROLES]
    if not all(ordered_passwords):
        raise ValueError("Day 4 writer passwords must be non-empty")
    if len(set(ordered_passwords)) != len(ordered_passwords):
        raise ValueError("Day 4 writer passwords must be distinct")

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
        role_list = ", ".join(_WRITER_ROLES)
        cursor.execute(
            f"GRANT CONNECT ON DATABASE commerce_analyst TO {role_list}"
        )
        cursor.execute(
            f"REVOKE CREATE ON DATABASE commerce_analyst FROM {role_list}"
        )
        cursor.execute(
            f"REVOKE TEMPORARY ON DATABASE commerce_analyst FROM {role_list}"
        )


def main() -> None:
    """Bootstrap the reviewed roles from explicit environment values."""

    bootstrap_day4_roles(
        os.environ["PRODUCT_POSTGRES_ADMIN_DSN"],
        {
            role_name: os.environ[environment_name]
            for role_name, environment_name in _PASSWORD_ENVIRONMENT_NAMES.items()
        },
    )
    print("Day 4 operation roles bootstrapped")


if __name__ == "__main__":
    main()
