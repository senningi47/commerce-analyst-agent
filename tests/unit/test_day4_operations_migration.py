import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

MIGRATION_PATH = Path("db/migrations/versions/0004_day4_product_operations.py")


class RecordingOperations:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.tables: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.indexes: list[
            tuple[str, str, tuple[str, ...], dict[str, object]]
        ] = []

    def execute(self, statement: str) -> None:
        self.sql.append(str(statement))

    def create_table(self, name: str, *items: object, **kwargs: object) -> None:
        self.tables.append((name, items, kwargs))

    def create_index(
        self,
        name: str,
        table_name: str,
        columns: list[str],
        **kwargs: object,
    ) -> None:
        self.indexes.append((name, table_name, tuple(columns), kwargs))


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "day4_product_operations_migration", MIGRATION_PATH
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


def table_items(
    operations: RecordingOperations, name: str
) -> tuple[tuple[object, ...], dict[str, object]]:
    matches = [(items, kwargs) for table, items, kwargs in operations.tables if table == name]
    assert len(matches) == 1
    return matches[0]


def column_names(items: tuple[object, ...]) -> set[str]:
    return {item.name for item in items if isinstance(item, sa.Column)}


def constraint_names(items: tuple[object, ...]) -> set[str]:
    return {
        item.name
        for item in items
        if isinstance(item, sa.Constraint) and item.name is not None
    }


def test_day4_migration_revision_and_object_manifest() -> None:
    module, operations = recorded_upgrade()

    assert module.revision == "0004_day4_product_operations"
    assert module.down_revision == "0003_day3_runtime_state"
    assert {name for name, _items, _kwargs in operations.tables} == {
        "operation_proposal",
        "approval_decision",
        "approval_nonce",
        "command_execution",
        "investigation_task",
        "investigation_conclusion",
        "risk_annotation",
        "metric_alert_rule",
        "alert_backtest_result",
        "audit_event",
        "product_trace_event",
    }


def test_day4_migration_tables_enforce_persistence_contracts() -> None:
    _module, operations = recorded_upgrade()
    schemas = {name: kwargs["schema"] for name, _items, kwargs in operations.tables}
    assert schemas == {
        "operation_proposal": "ops",
        "approval_decision": "ops",
        "approval_nonce": "ops",
        "command_execution": "ops",
        "investigation_task": "ops",
        "investigation_conclusion": "ops",
        "risk_annotation": "ops",
        "metric_alert_rule": "ops",
        "alert_backtest_result": "ops",
        "audit_event": "ops",
        "product_trace_event": "app",
    }

    expected_columns = {
        "operation_proposal": {
            "proposal_id",
            "proposal_version",
            "requester_id",
            "command_type",
            "command_schema_version",
            "canonical_payload",
            "payload_sha256",
            "evidence_refs",
            "preview",
            "target_versions",
            "target_versions_sha256",
            "status",
            "expires_at",
            "idempotency_key",
            "private_seller_id",
            "private_evidence_digest",
            "scenario_id",
            "created_at",
            "updated_at",
        },
        "approval_decision": {
            "proposal_id",
            "proposal_version",
            "decision",
            "requester_id",
            "approver_id",
            "reason",
            "decided_at",
            "grant_sha256",
            "scenario_id",
        },
        "approval_nonce": {
            "nonce_digest",
            "proposal_id",
            "proposal_version",
            "approver_id",
            "payload_sha256",
            "target_versions_sha256",
            "key_version",
            "expires_at",
            "used_at",
            "scenario_id",
        },
        "command_execution": {
            "execution_id",
            "proposal_id",
            "proposal_version",
            "actor_id",
            "command_type",
            "status",
            "idempotency_key",
            "before_version",
            "after_version",
            "result_code",
            "public_summary",
            "audit_ref",
            "started_at",
            "committed_at",
            "scenario_id",
        },
        "risk_annotation": {
            "risk_ref",
            "seller_id",
            "seller_digest",
            "seller_ref",
            "status",
            "observed_from",
            "observed_to",
            "metric_ref",
            "metric_revision",
            "numerator",
            "denominator",
            "metric_value",
            "threshold",
            "evidence_refs",
            "task_ref",
            "version",
            "scenario_id",
        },
    }
    for table_name, required in expected_columns.items():
        items, _kwargs = table_items(operations, table_name)
        assert required <= column_names(items)

    required_constraints = {
        "operation_proposal": {
            "pk_operation_proposal",
            "uq_operation_proposal_requester_idempotency",
            "ck_operation_proposal_payload_sha256",
            "ck_operation_proposal_target_versions_sha256",
            "ck_operation_proposal_status",
            "ck_operation_proposal_private_binding",
            "ck_operation_proposal_scenario_id",
        },
        "approval_decision": {
            "pk_approval_decision",
            "fk_approval_decision_proposal",
            "ck_approval_decision_actor_separation",
            "ck_approval_decision_grant_digest",
        },
        "approval_nonce": {
            "pk_approval_nonce",
            "uq_approval_nonce_proposal",
            "fk_approval_nonce_decision",
            "ck_approval_nonce_digest",
        },
        "command_execution": {
            "pk_command_execution",
            "uq_command_execution_proposal",
            "fk_command_execution_proposal",
            "ck_command_execution_terminal_time",
        },
        "audit_event": {
            "pk_audit_event",
            "uq_audit_event_execution_type",
            "fk_audit_event_execution",
            "ck_audit_event_payload_sha256",
        },
        "product_trace_event": {
            "pk_product_trace_event",
            "uq_product_trace_scope_attempt_sequence",
            "ck_product_trace_run_scope_digest",
        },
    }
    for table_name, required in required_constraints.items():
        items, _kwargs = table_items(operations, table_name)
        assert required <= constraint_names(items)

    for _table_name, items, _kwargs in operations.tables:
        for item in items:
            if isinstance(item, sa.Column) and isinstance(item.type, sa.DateTime):
                assert item.type.timezone is True


def test_day4_migration_quotes_window_in_metric_alert_rule_check() -> None:
    _module, operations = recorded_upgrade()
    items, _kwargs = table_items(operations, "metric_alert_rule")
    window_check = next(
        item
        for item in items
        if isinstance(item, sa.CheckConstraint)
        and item.name == "ck_metric_alert_rule_window"
    )

    assert str(window_check.sqltext) == '"window" IN (\'week\',\'month\')'


def test_day4_migration_quotes_window_in_metric_alert_rule_view() -> None:
    _module, operations = recorded_upgrade()
    view_sql = next(
        statement
        for statement in operations.sql
        if statement.startswith(
            "CREATE VIEW ops_read.enabled_metric_alert_rules "
            "WITH (security_barrier=true) AS"
        )
    )

    assert 'rule."window"' in view_sql


def test_day4_migration_quotes_window_in_metric_alert_rule_insert() -> None:
    _module, operations = recorded_upgrade()
    function_sql = next(
        statement
        for statement in operations.sql
        if statement.startswith(
            "CREATE FUNCTION "
            "trusted_schema.execute_create_and_enable_metric_alert_rule"
        )
    )

    assert 'grain, "window", comparator' in function_sql


def test_day4_migration_creates_reviewed_lookup_indexes() -> None:
    _module, operations = recorded_upgrade()
    indexes = {
        (name, table, columns)
        for name, table, columns, _kwargs in operations.indexes
    }
    assert {
        (
            "ix_operation_proposal_requester_status_expiry",
            "operation_proposal",
            ("requester_id", "status", "expires_at"),
        ),
        ("ix_approval_nonce_unused_expiry", "approval_nonce", ("expires_at",)),
        (
            "ix_command_execution_proposal_status",
            "command_execution",
            ("proposal_id", "proposal_version", "status"),
        ),
        (
            "ix_approval_decision_scenario_id",
            "approval_decision",
            ("scenario_id",),
        ),
        (
            "ix_investigation_task_status_assignee",
            "investigation_task",
            ("status", "assignee_ref"),
        ),
        (
            "ix_risk_annotation_seller_status",
            "risk_annotation",
            ("seller_digest", "status"),
        ),
        (
            "ix_metric_alert_rule_status_spec",
            "metric_alert_rule",
            ("status", "rule_spec_sha256"),
        ),
        (
            "ix_audit_event_execution_time",
            "audit_event",
            ("command_execution_id", "occurred_at"),
        ),
        (
            "ix_product_trace_scope_attempt_sequence",
            "product_trace_event",
            ("run_scope_digest", "attempt_id", "sequence"),
        ),
    } <= indexes


def test_day4_migration_hardens_every_security_definer_function() -> None:
    _module, operations = recorded_upgrade()
    sql = "\n".join(operations.sql)
    required = {
        "trusted_schema.create_operation_proposal",
        "trusted_schema.record_approval_decision",
        "trusted_schema.store_alert_backtest",
        "trusted_schema.find_seller_target_candidates",
        "trusted_schema.read_late_delivery_backtest",
        "trusted_schema.read_low_rating_backtest",
        "trusted_schema.read_cancellation_backtest",
        "trusted_schema.read_operation_proposal",
        "trusted_schema.read_approval_decision",
        "trusted_schema.append_audit_event",
        "trusted_schema.execute_open_seller_risk_case",
        "trusted_schema.execute_create_investigation_task",
        "trusted_schema.execute_create_investigation_from_alert_hit",
        "trusted_schema.execute_create_and_enable_metric_alert_rule",
        "trusted_schema.execute_assign_investigation",
        "trusted_schema.execute_transition_investigation",
        "trusted_schema.execute_add_investigation_conclusion",
        "trusted_schema.execute_close_investigation",
        "trusted_schema.reset_product_scenario",
    }

    assert all(f"FUNCTION {name}" in sql for name in required)
    assert sql.count("SECURITY DEFINER") >= len(required)
    assert sql.count("SET search_path = trusted_schema, pg_temp") >= len(required)
    assert "EXECUTE format" not in sql
    assert "EXECUTE ON ALL FUNCTIONS" not in sql
    assert "GRANT EXECUTE ON FUNCTION" in sql


def test_day4_recovery_reads_are_exact_and_scenario_scoped() -> None:
    _module, operations = recorded_upgrade()
    proposal_read = next(
        statement
        for statement in operations.sql
        if statement.startswith("CREATE FUNCTION trusted_schema.read_operation_proposal")
    )
    decision_read = next(
        statement
        for statement in operations.sql
        if statement.startswith("CREATE FUNCTION trusted_schema.read_approval_decision")
    )

    assert "proposal.proposal_id = p_proposal_id" in proposal_read
    assert "proposal.proposal_version = p_proposal_version" in proposal_read
    assert "proposal.scenario_id = p_scenario_id" in proposal_read
    assert "decision.proposal_id = p_proposal_id" in decision_read
    assert "decision.proposal_version = p_proposal_version" in decision_read
    assert "decision.scenario_id = p_scenario_id" in decision_read
    assert "decision.grant_sha256" in decision_read
    assert "signature" not in decision_read


def test_day4_create_proposal_qualifies_version_one_conflict_lookup() -> None:
    _module, operations = recorded_upgrade()
    create_sql = next(
        statement
        for statement in operations.sql
        if statement.startswith(
            "CREATE FUNCTION trusted_schema.create_operation_proposal"
        )
    )

    assert (
        "SELECT 1 FROM ops.operation_proposal AS proposal\n"
        "         WHERE proposal.proposal_id = p_proposal_id"
        in create_sql
    )


def test_day4_create_proposal_qualifies_revision_update() -> None:
    _module, operations = recorded_upgrade()
    create_sql = next(
        statement
        for statement in operations.sql
        if statement.startswith(
            "CREATE FUNCTION trusted_schema.create_operation_proposal"
        )
    )

    assert "UPDATE ops.operation_proposal AS proposal" in create_sql
    assert create_sql.count(
        "WHERE proposal.proposal_id = p_proposal_id"
    ) == 3


def test_day4_command_functions_enforce_atomic_execution_invariants() -> None:
    _module, operations = recorded_upgrade()
    command_sql = [
        statement
        for statement in operations.sql
        if statement.startswith("CREATE FUNCTION trusted_schema.execute_")
    ]
    assert len(command_sql) == 8
    for statement in command_sql:
        assert "FOR UPDATE" in statement
        assert "v_proposal.payload_sha256 <> p_payload_sha256" in statement
        assert "v_decision.approver_id <> p_actor_id" in statement
        assert "INSERT INTO ops.command_execution" in statement
        assert "UPDATE ops.approval_nonce" in statement
        assert "used_at IS NULL" in statement
        assert "expires_at > transaction_timestamp()" in statement
        assert "RETURNING nonce_digest INTO v_claimed_nonce" in statement
        assert "UPDATE ops.command_execution AS execution" in statement
        assert "WHERE execution.execution_id = p_execution_id" in statement
        assert "PERFORM trusted_schema.append_audit_event" in statement

    audit_sql = next(
        statement
        for statement in operations.sql
        if statement.startswith("CREATE FUNCTION trusted_schema.append_audit_event")
    )
    assert "execution.status = 'succeeded'" in audit_sql
    assert "execution.proposal_id = p_proposal_id" in audit_sql
    assert "execution.actor_id = p_actor_id" in audit_sql
    assert "proposal.payload_sha256 = p_payload_sha256" in audit_sql
    assert "ON CONFLICT (command_execution_id, event_type) DO NOTHING" in audit_sql
    assert "FOR UPDATE" not in audit_sql


def test_day4_fixed_read_functions_have_no_general_sql_escape_hatch() -> None:
    _module, operations = recorded_upgrade()
    sql = "\n".join(operations.sql)
    for name in (
        "find_seller_target_candidates",
        "read_late_delivery_backtest",
        "read_low_rating_backtest",
        "read_cancellation_backtest",
    ):
        statement = next(
            item
            for item in operations.sql
            if item.startswith(f"CREATE FUNCTION trusted_schema.{name}")
        )
        assert "SECURITY DEFINER" in statement
        assert "retail." in statement
        assert "EXECUTE " not in statement
        assert "format(" not in statement
    assert "p_sql" not in sql


def test_day4_migration_creates_only_reviewed_ops_read_views() -> None:
    _module, operations = recorded_upgrade()
    expected_columns = {
        "investigation_tasks": {
            "task_ref",
            "status",
            "assignee_ref",
            "version",
            "evidence_summary",
        },
        "risk_annotations": {
            "risk_ref",
            "seller_ref",
            "status",
            "observed_from",
            "observed_to",
            "metric_ref",
            "metric_value",
        },
        "enabled_metric_alert_rules": {
            "rule_ref",
            "metric_ref",
            "metric_revision",
            "grain",
            "comparator",
            "threshold",
            "window",
            "version",
        },
        "enabled_metric_alert_hits": {
            "hit_ref",
            "rule_ref",
            "window_start",
            "window_end",
            "value",
            "coverage",
        },
    }
    view_sql = {
        name: next(
            statement
            for statement in operations.sql
            if statement.startswith(
                f"CREATE VIEW ops_read.{name} WITH (security_barrier=true) AS"
            )
        )
        for name in expected_columns
    }

    for name, columns in expected_columns.items():
        statement = view_sql[name]
        assert all(column in statement for column in columns)
        assert "canonical_payload" not in statement
        assert "nonce_digest" not in statement
        assert "actor_id" not in statement
    assert "seller_id" not in view_sql["risk_annotations"]
    assert "seller_digest" not in view_sql["risk_annotations"]
    assert "rule.status = 'enabled'" in view_sql["enabled_metric_alert_rules"]
    assert "rule.status = 'enabled'" in view_sql["enabled_metric_alert_hits"]
    hit_identity = (
        "md5(rule.rule_ref::text || ':' || "
        "(hit->>'window_started_at'))::uuid"
    )
    assert hit_identity in view_sql["enabled_metric_alert_hits"]
    alert_command = next(
        item
        for item in operations.sql
        if item.startswith(
            "CREATE FUNCTION trusted_schema.execute_create_investigation_from_alert_hit"
        )
    )
    assert f"{hit_identity} = p_alert_hit_ref" in " ".join(alert_command.split())


def test_day4_migration_has_deny_by_default_acl_statements() -> None:
    _module, operations = recorded_upgrade()
    sql = "\n".join(operations.sql)
    assert "REVOKE ALL ON SCHEMA ops FROM PUBLIC" in sql
    assert "REVOKE ALL ON SCHEMA trusted_schema FROM PUBLIC" in sql
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA ops FROM PUBLIC" in sql
    assert "REVOKE ALL ON ALL SEQUENCES IN SCHEMA ops FROM PUBLIC" in sql
    assert "REVOKE ALL ON ops.audit_event FROM proposal_writer" in sql
    assert "GRANT SELECT ON ops_read.investigation_tasks" in sql
    assert "TO agent_reader" in sql
    assert "GRANT INSERT, UPDATE, DELETE ON ops.audit_event" not in sql
    assert "GRANT INSERT ON app.product_trace_event TO trace_writer" in sql
    assert "GRANT DELETE ON ops.operation_proposal" in sql
    assert "TO scenario_reset_owner" in sql
    assert (
        "GRANT USAGE ON SCHEMA trusted_schema TO product_scenario_reset"
        in operations.sql
    )
    assert (
        "GRANT EXECUTE ON FUNCTION trusted_schema.reset_product_scenario(text) "
        "TO product_scenario_reset"
        in operations.sql
    )
    assert (
        "GRANT USAGE ON SCHEMA ops TO operation_executor, audit_owner, "
        "scenario_reset_owner"
        in operations.sql
    )
    reset_owner_ops_select = next(
        statement
        for statement in operations.sql
        if statement.startswith(
            "GRANT SELECT (scenario_id) ON ops.operation_proposal"
        )
    )
    for table_name in (
        "operation_proposal",
        "approval_decision",
        "approval_nonce",
        "command_execution",
        "investigation_task",
        "investigation_conclusion",
        "risk_annotation",
        "metric_alert_rule",
        "alert_backtest_result",
        "audit_event",
    ):
        assert f"ops.{table_name}" in reset_owner_ops_select
    assert (
        "GRANT SELECT (scenario_id) ON app.product_trace_event "
        "TO scenario_reset_owner"
        in operations.sql
    )
    assert "CREATE ROLE" not in sql
    for view_name in (
        "investigation_tasks",
        "risk_annotations",
        "enabled_metric_alert_rules",
        "enabled_metric_alert_hits",
    ):
        assert f"REVOKE ALL ON ops_read.{view_name} FROM PUBLIC" in sql


def test_day4_migration_assigns_reviewed_owners_and_exact_function_grants() -> None:
    _module, operations = recorded_upgrade()
    sql = "\n".join(operations.sql)
    for table_name in (
        "operation_proposal",
        "approval_decision",
        "approval_nonce",
        "command_execution",
        "investigation_task",
        "investigation_conclusion",
        "risk_annotation",
        "metric_alert_rule",
        "alert_backtest_result",
    ):
        assert f"ALTER TABLE ops.{table_name} OWNER TO ops_owner" in sql
    assert "ALTER TABLE ops.audit_event OWNER TO audit_owner" in sql
    assert "ALTER TABLE app.product_trace_event OWNER TO ops_owner" in sql
    for view_name in (
        "investigation_tasks",
        "risk_annotations",
        "enabled_metric_alert_rules",
        "enabled_metric_alert_hits",
    ):
        assert f"ALTER VIEW ops_read.{view_name} OWNER TO ops_owner" in sql

    assert "OWNER TO audit_owner" in next(
        item for item in operations.sql if "append_audit_event(" in item and "ALTER" in item
    )
    assert "OWNER TO retail_owner" in next(
        item
        for item in operations.sql
        if "find_seller_target_candidates(" in item and "ALTER" in item
    )
    assert "OWNER TO scenario_reset_owner" in next(
        item
        for item in operations.sql
        if "reset_product_scenario(" in item and "ALTER" in item
    )

    assert "GRANT EXECUTE ON FUNCTION trusted_schema.create_operation_proposal" in sql
    assert "TO proposal_writer" in sql
    assert "GRANT EXECUTE ON FUNCTION trusted_schema.record_approval_decision" in sql
    assert "TO approval_writer" in sql
    assert "GRANT EXECUTE ON FUNCTION trusted_schema.read_operation_proposal" in sql
    assert "GRANT EXECUTE ON FUNCTION trusted_schema.read_approval_decision" in sql
    assert "GRANT EXECUTE ON FUNCTION trusted_schema.execute_close_investigation" in sql
    assert "TO operation_executor" in sql
    assert "EXECUTE ON ALL FUNCTIONS" not in sql


def test_day4_migration_downgrade_is_explicit_and_dependency_ordered() -> None:
    statements = recorded_downgrade()
    sql = "\n".join(statements)
    views = (
        "ops_read.investigation_tasks",
        "ops_read.risk_annotations",
        "ops_read.enabled_metric_alert_rules",
        "ops_read.enabled_metric_alert_hits",
    )
    functions = tuple(f"trusted_schema.{name}" for name in (
        "create_operation_proposal",
        "record_approval_decision",
        "store_alert_backtest",
        "find_seller_target_candidates",
        "read_late_delivery_backtest",
        "read_low_rating_backtest",
        "read_cancellation_backtest",
        "read_operation_proposal",
        "read_approval_decision",
        "append_audit_event",
        "execute_open_seller_risk_case",
        "execute_create_investigation_task",
        "execute_create_investigation_from_alert_hit",
        "execute_create_and_enable_metric_alert_rule",
        "execute_assign_investigation",
        "execute_transition_investigation",
        "execute_add_investigation_conclusion",
        "execute_close_investigation",
        "reset_product_scenario",
    ))
    tables = (
        "app.product_trace_event",
        "ops.audit_event",
        "ops.risk_annotation",
        "ops.investigation_conclusion",
        "ops.metric_alert_rule",
        "ops.command_execution",
        "ops.approval_nonce",
        "ops.approval_decision",
        "ops.operation_proposal",
        "ops.alert_backtest_result",
        "ops.investigation_task",
    )
    assert all(f"DROP VIEW IF EXISTS {name}" in sql for name in views)
    assert all(f"DROP FUNCTION IF EXISTS {name}" in sql for name in functions)
    assert all(f"DROP TABLE IF EXISTS {name}" in sql for name in tables)

    view_positions = [
        index for index, item in enumerate(statements) if item.startswith("DROP VIEW")
    ]
    function_positions = [
        index for index, item in enumerate(statements) if item.startswith("DROP FUNCTION")
    ]
    table_positions = [
        index for index, item in enumerate(statements) if item.startswith("DROP TABLE")
    ]
    assert max(view_positions) < min(function_positions)
    assert max(function_positions) < min(table_positions)
    assert max(table_positions) < statements.index("DROP SCHEMA trusted_schema")
    assert statements[-1] == "DROP SCHEMA ops"
    assert "DROP SCHEMA app" not in sql
    for protected_schema in ("retail", "knowledge", "checkpoint", "model_state"):
        assert f"DROP SCHEMA {protected_schema}" not in sql
