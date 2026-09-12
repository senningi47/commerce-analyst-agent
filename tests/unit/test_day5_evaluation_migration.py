import importlib.util
from pathlib import Path
from types import ModuleType

MIGRATION_PATH = Path("db/migrations/versions/0005_day5_evaluation_schema.py")


class RecordingOperations:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, statement: str) -> None:
        self.sql.append(str(statement))


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "day5_evaluation_schema_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def recorded_upgrade() -> tuple[ModuleType, RecordingOperations]:
    module = load_migration()
    operations = RecordingOperations()
    module.op = operations
    module.upgrade()
    return module, operations


def recorded_downgrade() -> list[str]:
    module = load_migration()
    operations = RecordingOperations()
    module.op = operations
    module.downgrade()
    return operations.sql


def test_day5_migration_revision_chain() -> None:
    module, _operations = recorded_upgrade()

    assert module.revision == "0005_day5_evaluation_schema"
    assert module.down_revision == "0004_day4_product_operations"


def test_day5_upgrade_creates_eval_schema_and_four_tables() -> None:
    _module, operations = recorded_upgrade()
    statements = operations.sql

    assert "CREATE SCHEMA eval" in statements
    for table in ("experiment", "task_attempt", "task_result", "bird_trace_event"):
        assert any(f"CREATE TABLE eval.{table} (" in statement for statement in statements)
    assert any("REFERENCES eval.experiment(experiment_id)" in s for s in statements)
    assert any("REFERENCES eval.task_attempt(attempt_id)" in s for s in statements)


def test_day5_status_and_identity_constraints_are_frozen() -> None:
    _module, operations = recorded_upgrade()
    joined = "\n".join(operations.sql)

    assert "purpose IN ('pilot','full','ablation_repair','rag_ab','product')" in joined
    assert "mode IN ('c','a')" in joined
    assert (
        "status IN ('pending','running','succeeded','failed',"
        "'infrastructure_error','interrupted')" in joined
    )
    assert "UNIQUE (run_id, task_id, mode, attempt_seq)" in joined
    assert "PRIMARY KEY (attempt_id, phase, sequence)" in joined


def test_day5_ownership_and_least_privilege_grants() -> None:
    _module, operations = recorded_upgrade()
    joined = "\n".join(operations.sql)

    assert "ALTER SCHEMA eval OWNER TO evaluation_owner" in joined
    assert joined.count("OWNER TO evaluation_owner") == 5
    assert "REVOKE ALL ON SCHEMA eval FROM PUBLIC" in joined
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA eval FROM PUBLIC" in joined
    assert "GRANT USAGE ON SCHEMA eval TO evaluation_writer" in joined
    assert (
        "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA eval TO evaluation_writer"
        in joined
    )
    # least privilege: the writer never deletes, and no definer functions exist
    assert "DELETE" not in joined
    assert "SECURITY DEFINER" not in joined


def test_day5_downgrade_drops_schema() -> None:
    statements = recorded_downgrade()

    assert statements == ["DROP SCHEMA eval CASCADE"]
