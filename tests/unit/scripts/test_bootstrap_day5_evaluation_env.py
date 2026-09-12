from collections.abc import Mapping
from typing import Self

import pytest

from scripts.bootstrap_day5_evaluation_env import bootstrap_day5_evaluation_roles, main

OWNER_ROLES = ("evaluation_owner",)
WRITER_ROLES = ("evaluation_writer",)


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


def test_bootstrap_creates_hardened_owner_and_writer_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(existing_roles=set())
    monkeypatch.setattr(
        "psycopg.connect", lambda *args, **kwargs: FakeConnection(cursor)
    )

    bootstrap_day5_evaluation_roles("postgresql://admin", distinct_passwords())

    rendered = _rendered(cursor)
    assert any(
        "CREATE ROLE" in statement
        and '"evaluation_owner"' in statement
        and "NOLOGIN NOINHERIT" in statement
        and "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS" in statement
        for statement in rendered
    )
    assert any(
        "CREATE ROLE" in statement
        and '"evaluation_writer"' in statement
        and "LOGIN NOINHERIT" in statement
        for statement in rendered
    )
    assert any(
        "PASSWORD" in statement and "private-1" in statement
        for statement in rendered
    )
    assert any(
        "PASSWORD" in statement and "private-1" in statement
        for statement in rendered
    )
    assert 'ALTER ROLE "evaluation_writer" CONNECTION LIMIT 4' in rendered
    assert "ALTER ROLE \"evaluation_writer\" SET temp_file_limit = '64MB'" in rendered


def test_bootstrap_is_idempotent_through_alter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(existing_roles=set(OWNER_ROLES) | set(WRITER_ROLES))
    monkeypatch.setattr(
        "psycopg.connect", lambda *args, **kwargs: FakeConnection(cursor)
    )

    bootstrap_day5_evaluation_roles("postgresql://admin", distinct_passwords())

    rendered = _rendered(cursor)
    assert any(
        "ALTER ROLE" in statement
        and '"evaluation_owner"' in statement
        and "NOLOGIN" in statement
        for statement in rendered
    )
    assert any(
        "ALTER ROLE" in statement
        and '"evaluation_writer"' in statement
        and "LOGIN" in statement
        for statement in rendered
    )


def test_bootstrap_grants_connect_and_revokes_create_and_temporary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(existing_roles=set())
    monkeypatch.setattr(
        "psycopg.connect", lambda *args, **kwargs: FakeConnection(cursor)
    )

    bootstrap_day5_evaluation_roles("postgresql://admin", distinct_passwords())

    rendered = _rendered(cursor)
    assert "GRANT CONNECT ON DATABASE commerce_analyst TO evaluation_writer" in rendered
    assert "REVOKE CREATE ON DATABASE commerce_analyst FROM evaluation_writer" in rendered
    assert "REVOKE TEMPORARY ON DATABASE commerce_analyst FROM evaluation_writer" in rendered


def test_bootstrap_revokes_stray_memberships(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor(
        existing_roles=set(),
        memberships=(("evaluation_owner", "evaluation_writer"),),
    )
    monkeypatch.setattr(
        "psycopg.connect", lambda *args, **kwargs: FakeConnection(cursor)
    )

    bootstrap_day5_evaluation_roles("postgresql://admin", distinct_passwords())

    assert 'REVOKE "evaluation_owner" FROM "evaluation_writer"' in _rendered(cursor)


def test_bootstrap_rejects_invalid_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor(existing_roles=set())
    monkeypatch.setattr(
        "psycopg.connect", lambda *args, **kwargs: FakeConnection(cursor)
    )

    with pytest.raises(ValueError, match="PRODUCT_POSTGRES_ADMIN_DSN"):
        bootstrap_day5_evaluation_roles("", distinct_passwords())
    with pytest.raises(ValueError, match="exactly the reviewed Day 5 writer roles"):
        bootstrap_day5_evaluation_roles("postgresql://admin", {"other": "x"})
    with pytest.raises(ValueError, match="must be non-empty"):
        bootstrap_day5_evaluation_roles("postgresql://admin", {"evaluation_writer": ""})


def test_main_uses_reviewed_environment_names(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Mapping[str, str]] = {}

    def fake_bootstrap(admin_dsn: str, passwords: Mapping[str, str]) -> None:
        seen["dsn"] = admin_dsn
        seen["passwords"] = passwords

    monkeypatch.setenv("PRODUCT_POSTGRES_ADMIN_DSN", "postgresql://admin")
    monkeypatch.setenv("PRODUCT_EVALUATION_WRITER_PASSWORD", "private-1")
    monkeypatch.setattr(
        "scripts.bootstrap_day5_evaluation_env.bootstrap_day5_evaluation_roles",
        fake_bootstrap,
    )
    main()

    assert seen["passwords"] == {"evaluation_writer": "private-1"}
