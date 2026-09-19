import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

MIGRATION_PATH = Path("db/migrations/versions/0003_day3_runtime_state.py")


class RecordingOperations:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.tables: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def execute(self, statement: str) -> None:
        self.sql.append(statement)

    def create_table(self, name: str, *items: object, **kwargs: object) -> None:
        self.tables.append((name, items, kwargs))


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("day3_runtime_state_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_state_migration_emits_private_schema_and_acl_baseline() -> None:
    module = load_migration()
    operations = RecordingOperations()
    module.op = operations

    module.upgrade()

    assert module.revision == "0003_day3_runtime_state"
    assert module.down_revision == "0002_knowledge_baseline"
    assert operations.sql[:2] == [
        "CREATE SCHEMA checkpoint AUTHORIZATION checkpoint_owner",
        "CREATE SCHEMA model_state AUTHORIZATION model_state_owner",
    ]
    assert len(operations.tables) == 1
    table_name, items, kwargs = operations.tables[0]
    assert table_name == "provider_turn"
    assert kwargs == {"schema": "model_state"}
    columns = {item.name for item in items if isinstance(item, sa.Column)}
    assert columns == {
        "turn_id",
        "scope_digest",
        "attempt_id",
        "sequence",
        "provider",
        "model",
        "payload",
        "payload_sha256",
        "expected_tool_call_ids",
        "token_weight",
        "created_at",
        "last_used_at",
        "absolute_expires_at",
        "terminal_at",
    }
    constraints = {
        item.name
        for item in items
        if isinstance(item, sa.Constraint) and item.name is not None
    }
    assert {
        "ck_provider_turn_absolute_expiry",
        "ck_provider_turn_last_used_at",
        "ck_provider_turn_payload_sha256",
        "ck_provider_turn_scope_digest",
        "ck_provider_turn_sequence",
        "ck_provider_turn_terminal_payload",
        "ck_provider_turn_token_weight",
        "pk_provider_turn",
        "uq_provider_turn_scope_attempt_sequence",
    } <= constraints
    assert "REVOKE ALL ON SCHEMA checkpoint FROM PUBLIC" in operations.sql
    assert "REVOKE ALL ON SCHEMA model_state FROM PUBLIC" in operations.sql
    assert "GRANT USAGE ON SCHEMA model_state TO model_state_writer" in operations.sql
    assert not any("GRANT" in statement and "checkpoint" in statement for statement in operations.sql)
    assert not columns & {"api_key", "dsn", "request", "product", "bird"}
