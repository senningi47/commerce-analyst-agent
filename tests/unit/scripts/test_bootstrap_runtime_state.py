from typing import Self

import pytest
from psycopg.rows import dict_row
from pydantic import SecretStr

from scripts.bootstrap_runtime_state import (
    RuntimeStateBootstrapError,
    apply_runtime_acls,
    bootstrap_runtime_roles,
    checkpoint_object_manifest,
    main,
    setup_checkpoint_objects,
)


class FakeCursor:
    def __init__(self, existing_roles: set[str]) -> None:
        self.existing_roles = existing_roles
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


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self._cursor


def test_checkpoint_manifest_matches_pinned_public_package() -> None:
    manifest = checkpoint_object_manifest()

    assert manifest.package == "langgraph-checkpoint-postgres"
    assert manifest.package_version == "3.1.2"
    assert manifest.tables == (
        "checkpoint_migrations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
    )
    assert manifest.indexes == (
        "checkpoints_thread_id_idx",
        "checkpoint_blobs_thread_id_idx",
        "checkpoint_writes_thread_id_idx",
    )


@pytest.mark.asyncio
async def test_setup_rejects_unreviewed_package_version_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def fake_connect(*_args: object, **_kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("connect must not be called")

    monkeypatch.setattr("scripts.bootstrap_runtime_state.version", lambda _name: "3.1.3")

    with pytest.raises(RuntimeStateBootstrapError) as caught:
        await setup_checkpoint_objects(SecretStr("admin-dsn"), connect=fake_connect)

    assert caught.value.reason_code == "checkpoint_package_version_mismatch"
    assert called is False


@pytest.mark.asyncio
async def test_setup_uses_admin_connection_and_fixed_search_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAsyncConnection:
        def __init__(self) -> None:
            self.setup_calls = 0

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    connection = FakeAsyncConnection()
    connect_calls: list[tuple[str, dict[str, object]]] = []

    async def fake_connect(dsn: str, **kwargs: object) -> FakeAsyncConnection:
        connect_calls.append((dsn, kwargs))
        return connection

    class FakeSaver:
        def __init__(self, observed_connection: FakeAsyncConnection) -> None:
            assert observed_connection is connection

        async def setup(self) -> None:
            connection.setup_calls += 1

    monkeypatch.setattr(
        "scripts.bootstrap_runtime_state.AsyncPostgresSaver",
        FakeSaver,
    )

    await setup_checkpoint_objects(SecretStr("admin-dsn"), connect=fake_connect)

    assert connect_calls == [
        (
            "admin-dsn",
            {
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
                "options": "-csearch_path=checkpoint,pg_catalog",
            },
        )
    ]
    assert connection.setup_calls == 1


@pytest.mark.asyncio
async def test_setup_rejects_empty_admin_dsn_before_connecting() -> None:
    called = False

    async def fake_connect(*_args: object, **_kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("connect must not be called")

    with pytest.raises(ValueError, match="PRODUCT_POSTGRES_ADMIN_DSN"):
        await setup_checkpoint_objects(SecretStr(""), connect=fake_connect)

    assert called is False


@pytest.mark.asyncio
async def test_runtime_acls_reject_unexpected_objects_before_mutation() -> None:
    class FakeAclCursor:
        def __init__(self) -> None:
            self.statements: list[str] = []

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def execute(self, query: object, params: object = None) -> None:
            rendered = query if isinstance(query, str) else query.as_string(None)
            self.statements.append(rendered)

        async def fetchall(self) -> list[dict[str, str]]:
            manifest = checkpoint_object_manifest()
            return [
                *(
                    {"object_name": table, "object_type": "table"}
                    for table in manifest.tables
                ),
                *(
                    {"object_name": index, "object_type": "index"}
                    for index in manifest.indexes
                ),
                {"object_name": "unexpected_table", "object_type": "table"},
            ]

    class FakeAclConnection:
        def __init__(self, cursor: FakeAclCursor) -> None:
            self._cursor = cursor

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def cursor(self) -> FakeAclCursor:
            return self._cursor

        class Transaction:
            async def __aenter__(self) -> Self:
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

        def transaction(self) -> Transaction:
            return self.Transaction()

    cursor = FakeAclCursor()

    async def fake_connect(*_args: object, **_kwargs: object) -> FakeAclConnection:
        return FakeAclConnection(cursor)

    with pytest.raises(RuntimeStateBootstrapError) as caught:
        await apply_runtime_acls(SecretStr("admin-dsn"), connect=fake_connect)

    assert caught.value.reason_code == "checkpoint_manifest_mismatch"
    assert not any(
        statement.startswith(("ALTER ", "REVOKE ", "GRANT "))
        for statement in cursor.statements
    )


@pytest.mark.asyncio
async def test_runtime_acls_transfer_ownership_and_grant_only_runtime_dml() -> None:
    manifest = checkpoint_object_manifest()

    class FakeAclCursor:
        def __init__(self) -> None:
            self.statements: list[str] = []

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def execute(self, query: object, params: object = None) -> None:
            rendered = query if isinstance(query, str) else query.as_string(None)
            self.statements.append(rendered)

        async def fetchall(self) -> list[dict[str, str]]:
            return [
                *(
                    {"object_name": table, "object_type": "table"}
                    for table in manifest.tables
                ),
                *(
                    {"object_name": index, "object_type": "index"}
                    for index in manifest.indexes
                ),
            ]

    class FakeTransaction:
        def __init__(self) -> None:
            self.entered = False
            self.exited = False

        async def __aenter__(self) -> Self:
            self.entered = True
            return self

        async def __aexit__(self, *args: object) -> None:
            self.exited = True

    class FakeAclConnection:
        def __init__(self, cursor: FakeAclCursor, transaction: FakeTransaction) -> None:
            self._cursor = cursor
            self._transaction = transaction

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def cursor(self) -> FakeAclCursor:
            return self._cursor

        def transaction(self) -> FakeTransaction:
            return self._transaction

    cursor = FakeAclCursor()
    transaction = FakeTransaction()

    async def fake_connect(*_args: object, **_kwargs: object) -> FakeAclConnection:
        return FakeAclConnection(cursor, transaction)

    await apply_runtime_acls(SecretStr("admin-dsn"), connect=fake_connect)

    assert transaction.entered is True
    assert transaction.exited is True
    for table in manifest.tables:
        assert (
            f'ALTER TABLE "checkpoint"."{table}" OWNER TO "checkpoint_owner"'
            in cursor.statements
        )
    assert (
        "GRANT USAGE ON SCHEMA checkpoint TO checkpoint_writer" in cursor.statements
    )
    assert (
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
        "checkpoint.checkpoints, checkpoint.checkpoint_blobs, "
        "checkpoint.checkpoint_writes TO checkpoint_writer"
    ) in cursor.statements
    assert not any(
        statement.startswith("GRANT") and "checkpoint_migrations" in statement
        for statement in cursor.statements
    )


def test_checkpoint_cli_runs_setup_then_acls_without_printing_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_setup(admin_dsn: SecretStr) -> None:
        calls.append(("setup", admin_dsn.get_secret_value()))

    async def fake_acls(admin_dsn: SecretStr) -> None:
        calls.append(("acls", admin_dsn.get_secret_value()))

    monkeypatch.setenv("PRODUCT_POSTGRES_ADMIN_DSN", "private-admin-dsn")
    monkeypatch.setattr(
        "scripts.bootstrap_runtime_state.setup_checkpoint_objects",
        fake_setup,
    )
    monkeypatch.setattr("scripts.bootstrap_runtime_state.apply_runtime_acls", fake_acls)

    main(["checkpoint"])

    assert calls == [
        ("setup", "private-admin-dsn"),
        ("acls", "private-admin-dsn"),
    ]
    output = capsys.readouterr().out
    assert output.strip() == "Checkpoint objects and ACLs bootstrapped"
    assert "private-admin-dsn" not in output


@pytest.mark.parametrize(
    ("admin", "checkpoint_password", "model_password", "message"),
    [
        ("", "checkpoint", "model", "ADMIN_DSN"),
        ("dsn", "", "model", "CHECKPOINT_WRITER_PASSWORD"),
        ("dsn", "checkpoint", "", "MODEL_STATE_WRITER_PASSWORD"),
        ("dsn", "same", "same", "must differ"),
    ],
)
def test_runtime_role_bootstrap_rejects_invalid_secrets_before_connecting(
    admin: str,
    checkpoint_password: str,
    model_password: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        bootstrap_runtime_roles(admin, checkpoint_password, model_password)


def test_runtime_role_bootstrap_creates_four_hardened_roles_without_object_grants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(existing_roles=set())
    connection = FakeConnection(cursor)
    connect_calls: list[tuple[str, bool]] = []

    def connect(admin_dsn: str, *, autocommit: bool) -> FakeConnection:
        connect_calls.append((admin_dsn, autocommit))
        return connection

    monkeypatch.setattr("scripts.bootstrap_runtime_state.psycopg.connect", connect)

    bootstrap_runtime_roles("admin-dsn", "checkpoint-password", "model-password")

    assert connect_calls == [("admin-dsn", True)]
    commands = [statement for statement, _ in cursor.statements]
    for owner in ("checkpoint_owner", "model_state_owner"):
        assert any(
            command.startswith(f'CREATE ROLE "{owner}" NOLOGIN')
            and "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS" in command
            for command in commands
        )
    for writer in ("checkpoint_writer", "model_state_writer"):
        assert any(
            command.startswith(f'CREATE ROLE "{writer}" LOGIN')
            and "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS" in command
            for command in commands
        )
        assert f'ALTER ROLE "{writer}" CONNECTION LIMIT 4' in commands
        assert f'ALTER ROLE "{writer}" SET temp_file_limit = \'64MB\'' in commands
    assert (
        "GRANT CONNECT ON DATABASE commerce_analyst TO checkpoint_writer, model_state_writer"
        in commands
    )
    assert (
        "REVOKE TEMPORARY ON DATABASE commerce_analyst FROM "
        "checkpoint_writer, model_state_writer"
    ) in commands
    assert not any(" ON SCHEMA " in command or " ON TABLE " in command for command in commands)


def test_runtime_role_bootstrap_hardens_existing_roles_idempotently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roles = {
        "checkpoint_owner",
        "model_state_owner",
        "checkpoint_writer",
        "model_state_writer",
    }
    cursor = FakeCursor(existing_roles=roles)
    monkeypatch.setattr(
        "scripts.bootstrap_runtime_state.psycopg.connect",
        lambda *_args, **_kwargs: FakeConnection(cursor),
    )

    bootstrap_runtime_roles("admin-dsn", "checkpoint-password", "model-password")

    commands = [statement for statement, _ in cursor.statements]
    assert not any(command.startswith("CREATE ROLE") for command in commands)
    for role in roles:
        assert any(command.startswith(f'ALTER ROLE "{role}"') for command in commands)
