from collections.abc import Mapping
from pathlib import Path
from typing import Self

import pytest

from scripts.bootstrap_day4_operations import bootstrap_day4_roles, main

OWNER_ROLES = ("ops_owner", "audit_owner", "scenario_reset_owner")
WRITER_ROLES = (
    "proposal_writer",
    "approval_writer",
    "operation_executor",
    "trace_writer",
    "product_scenario_reset",
)


def distinct_passwords() -> dict[str, str]:
    return {role: f"private-{index}" for index, role in enumerate(WRITER_ROLES, start=1)}


class FakeCursor:
    def __init__(
        self,
        existing_roles: set[str],
        memberships: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.existing_roles = existing_roles
        self.memberships = memberships
        self.statements: list[tuple[str, object]] = []
        self._result: tuple[bool] | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: object, params: object = None) -> None:
        rendered = query if isinstance(query, str) else query.as_string(None)
        self.statements.append((rendered, params))
        if rendered.startswith("SELECT EXISTS"):
            assert isinstance(params, tuple)
            self._result = (params[0] in self.existing_roles,)

    def fetchone(self) -> tuple[bool] | None:
        return self._result

    def fetchall(self) -> tuple[tuple[str, str], ...]:
        return self.memberships


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self._cursor


def _rendered(cursor: FakeCursor) -> list[str]:
    return [statement for statement, _params in cursor.statements]


def test_role_bootstrap_creates_hardened_non_inheriting_roles(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cursor = FakeCursor(existing_roles=set())
    connection = FakeConnection(cursor)
    connect_calls: list[tuple[str, bool]] = []

    def fake_connect(admin_dsn: str, *, autocommit: bool) -> FakeConnection:
        connect_calls.append((admin_dsn, autocommit))
        return connection

    monkeypatch.setattr("scripts.bootstrap_day4_operations.psycopg.connect", fake_connect)

    bootstrap_day4_roles("admin-dsn", distinct_passwords())

    sql = _rendered(cursor)
    negative_capabilities = (
        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
    )
    assert connect_calls == [("admin-dsn", True)]
    for owner in OWNER_ROLES:
        assert (
            f'CREATE ROLE "{owner}" NOLOGIN NOINHERIT {negative_capabilities}' in sql
        )
    for writer in WRITER_ROLES:
        assert any(
            statement.startswith(f'CREATE ROLE "{writer}" LOGIN NOINHERIT ')
            and negative_capabilities in statement
            for statement in sql
        )
        assert f'ALTER ROLE "{writer}" CONNECTION LIMIT 4' in sql
        assert f'ALTER ROLE "{writer}" SET temp_file_limit = \'64MB\'' in sql
    assert (
        "GRANT CONNECT ON DATABASE commerce_analyst TO " + ", ".join(WRITER_ROLES)
    ) in sql
    assert (
        "REVOKE CREATE ON DATABASE commerce_analyst FROM " + ", ".join(WRITER_ROLES)
    ) in sql
    assert (
        "REVOKE TEMPORARY ON DATABASE commerce_analyst FROM " + ", ".join(WRITER_ROLES)
    ) in sql
    assert not any(" ON SCHEMA " in statement for statement in sql)
    assert not any(" ON TABLE " in statement for statement in sql)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("admin_dsn", "passwords", "message"),
    [
        ("", distinct_passwords(), "ADMIN_DSN"),
        (
            "admin-dsn",
            {role: "secret" for role in WRITER_ROLES[:-1]},
            "exactly",
        ),
        (
            "admin-dsn",
            {**distinct_passwords(), "unexpected_writer": "secret"},
            "exactly",
        ),
        (
            "admin-dsn",
            {**distinct_passwords(), "proposal_writer": ""},
            "non-empty",
        ),
        (
            "admin-dsn",
            {
                **distinct_passwords(),
                "proposal_writer": "shared",
                "approval_writer": "shared",
            },
            "distinct",
        ),
    ],
)
def test_role_bootstrap_rejects_invalid_inputs_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
    admin_dsn: str,
    passwords: Mapping[str, str],
    message: str,
) -> None:
    def unexpected_connect(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("invalid inputs must fail before connecting")

    monkeypatch.setattr(
        "scripts.bootstrap_day4_operations.psycopg.connect",
        unexpected_connect,
    )

    with pytest.raises(ValueError, match=message):
        bootstrap_day4_roles(admin_dsn, passwords)


def test_role_bootstrap_removes_all_memberships_involving_day4_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(
        existing_roles={*OWNER_ROLES, *WRITER_ROLES},
        memberships=(
            ("legacy_parent", "proposal_writer"),
            ("ops_owner", "legacy_member"),
        ),
    )
    monkeypatch.setattr(
        "scripts.bootstrap_day4_operations.psycopg.connect",
        lambda *_args, **_kwargs: FakeConnection(cursor),
    )

    bootstrap_day4_roles("admin-dsn", distinct_passwords())

    statements = cursor.statements
    assert any(
        statement.startswith("SELECT granted.rolname, member.rolname")
        for statement, _params in statements
    )
    sql = _rendered(cursor)
    assert 'REVOKE "legacy_parent" FROM "proposal_writer"' in sql
    assert 'REVOKE "ops_owner" FROM "legacy_member"' in sql


def test_role_bootstrap_hardens_existing_roles_idempotently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(existing_roles={*OWNER_ROLES, *WRITER_ROLES})
    monkeypatch.setattr(
        "scripts.bootstrap_day4_operations.psycopg.connect",
        lambda *_args, **_kwargs: FakeConnection(cursor),
    )

    bootstrap_day4_roles("admin-dsn", distinct_passwords())

    sql = _rendered(cursor)
    assert not any(statement.startswith("CREATE ROLE") for statement in sql)
    for owner in OWNER_ROLES:
        assert any(statement.startswith(f'ALTER ROLE "{owner}" NOLOGIN NOINHERIT') for statement in sql)
    for writer in WRITER_ROLES:
        assert any(statement.startswith(f'ALTER ROLE "{writer}" LOGIN NOINHERIT') for statement in sql)


def test_cli_passes_exact_environment_values_without_printing_them(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    passwords = distinct_passwords()
    monkeypatch.setenv("PRODUCT_POSTGRES_ADMIN_DSN", "private-admin-dsn")
    environment_names = {
        "proposal_writer": "PRODUCT_PROPOSAL_WRITER_PASSWORD",
        "approval_writer": "PRODUCT_APPROVAL_WRITER_PASSWORD",
        "operation_executor": "PRODUCT_OPERATION_EXECUTOR_PASSWORD",
        "trace_writer": "PRODUCT_TRACE_WRITER_PASSWORD",
        "product_scenario_reset": "PRODUCT_SCENARIO_RESET_PASSWORD",
    }
    for role_name, environment_name in environment_names.items():
        monkeypatch.setenv(environment_name, passwords[role_name])
    calls: list[tuple[str, Mapping[str, str]]] = []

    def fake_bootstrap(admin_dsn: str, received: Mapping[str, str]) -> None:
        calls.append((admin_dsn, received))

    monkeypatch.setattr(
        "scripts.bootstrap_day4_operations.bootstrap_day4_roles",
        fake_bootstrap,
    )

    main()

    assert calls == [("private-admin-dsn", passwords)]
    output = capsys.readouterr().out
    assert output == "Day 4 operation roles bootstrapped\n"
    assert "private-admin-dsn" not in output
    assert not any(password in output for password in passwords.values())


def test_bootstrap_source_never_imports_or_invokes_migration_execution() -> None:
    source = Path("scripts/bootstrap_day4_operations.py").read_text(encoding="utf-8")
    folded_source = source.casefold()

    assert "alembic" not in folded_source
    assert "upgrade" not in folded_source
    assert "0004_day4_product_operations" not in source
