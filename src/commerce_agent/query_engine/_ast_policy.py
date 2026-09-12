from dataclasses import dataclass

from sqlglot import ErrorLevel, exp, parse
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.tokens import Tokenizer, TokenType

from commerce_agent.query_engine.errors import SqlPolicyViolation

RETAIL_TABLE_COLUMNS = {
    "customers": frozenset(
        {
            "customer_id",
            "customer_unique_id",
            "customer_zip_code_prefix",
            "customer_city",
            "customer_state",
        }
    ),
    "orders": frozenset(
        {
            "order_id",
            "customer_id",
            "order_status",
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        }
    ),
    "order_items": frozenset(
        {
            "order_id",
            "order_item_id",
            "product_id",
            "seller_id",
            "shipping_limit_date",
            "price",
            "freight_value",
        }
    ),
    "order_payments": frozenset(
        {
            "order_id",
            "payment_sequential",
            "payment_type",
            "payment_installments",
            "payment_value",
        }
    ),
    "order_reviews": frozenset(
        {
            "review_row_id",
            "review_id",
            "order_id",
            "review_score",
            "review_comment_title",
            "review_comment_message",
            "review_creation_date",
            "review_answer_timestamp",
        }
    ),
    "products": frozenset(
        {
            "product_id",
            "product_category_name",
            "product_name_lenght",
            "product_description_lenght",
            "product_photos_qty",
            "product_weight_g",
            "product_length_cm",
            "product_height_cm",
            "product_width_cm",
        }
    ),
    "sellers": frozenset(
        {
            "seller_id",
            "seller_zip_code_prefix",
            "seller_city",
            "seller_state",
        }
    ),
    "product_category_name_translation": frozenset(
        {
            "product_category_name",
            "product_category_name_english",
        }
    ),
}

OPS_READ_TABLE_COLUMNS = {
    "investigation_tasks": frozenset(
        {"task_ref", "status", "assignee_ref", "version", "evidence_summary"}
    ),
    "risk_annotations": frozenset(
        {
            "risk_ref",
            "seller_ref",
            "status",
            "observed_from",
            "observed_to",
            "metric_ref",
            "metric_value",
        }
    ),
    "enabled_metric_alert_rules": frozenset(
        {
            "rule_ref",
            "metric_ref",
            "metric_revision",
            "grain",
            "comparator",
            "threshold",
            "window",
            "version",
        }
    ),
    "enabled_metric_alert_hits": frozenset(
        {"hit_ref", "rule_ref", "window_start", "window_end", "value", "coverage"}
    ),
}

ALLOWED_RELATIONS = {
    "retail": RETAIL_TABLE_COLUMNS,
    "ops_read": OPS_READ_TABLE_COLUMNS,
}

ALLOWED_TABLES = RETAIL_TABLE_COLUMNS

ALLOWED_JOIN_EDGES = frozenset(
    {
        frozenset({("orders", "customer_id"), ("customers", "customer_id")}),
        frozenset({("order_items", "order_id"), ("orders", "order_id")}),
        frozenset({("order_items", "product_id"), ("products", "product_id")}),
        frozenset({("order_items", "seller_id"), ("sellers", "seller_id")}),
        frozenset({("order_payments", "order_id"), ("orders", "order_id")}),
        frozenset({("order_reviews", "order_id"), ("orders", "order_id")}),
        frozenset(
            {
                ("products", "product_category_name"),
                (
                    "product_category_name_translation",
                    "product_category_name",
                ),
            }
        ),
    }
)

ALLOWED_FUNCTIONS = frozenset(
    {
        "AVG",
        "COALESCE",
        "COUNT",
        "EXTRACT",
        "LOWER",
        "MAX",
        "MIN",
        "NULLIF",
        "ROUND",
        "SUM",
        "TIMESTAMP_TRUNC",
        "UPPER",
    }
)


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str
    is_aggregate: bool


def _scope_sources(
    scope: Scope,
) -> tuple[dict[str, frozenset[str]], dict[str, str]]:
    source_columns: dict[str, frozenset[str]] = {}
    source_tables: dict[str, str] = {}
    for alias, source in scope.sources.items():
        if isinstance(source, exp.Table):
            if source.catalog:
                raise SqlPolicyViolation("catalog_denied", "Catalogs are not allowed")
            schema_name = source.db or "retail"
            if schema_name not in ALLOWED_RELATIONS:
                raise SqlPolicyViolation("schema_denied", "Schema is not allowed")
            table_name = source.name
            relations = ALLOWED_RELATIONS[schema_name]
            if table_name not in relations:
                raise SqlPolicyViolation("table_denied", "Table is not allowed")
            source_columns[alias] = relations[table_name]
            source_tables[alias] = (
                table_name if schema_name == "retail" else f"{schema_name}.{table_name}"
            )
        elif isinstance(source, Scope):
            source_columns[alias] = frozenset(source.expression.named_selects)
        else:
            raise SqlPolicyViolation("source_denied", "Query source is not understood")
    return source_columns, source_tables


def _validate_scope_columns(
    scope: Scope,
    source_columns: dict[str, frozenset[str]],
) -> None:
    for column in scope.columns:
        if column.is_star:
            continue
        if column.table:
            columns = source_columns.get(column.table)
            if columns is None or column.name not in columns:
                raise SqlPolicyViolation("column_denied", "Column is not allowed")
            continue
        matches = [columns for columns in source_columns.values() if column.name in columns]
        if len(matches) != 1:
            raise SqlPolicyViolation(
                "column_ambiguous",
                "Column is missing or ambiguous",
            )


def _validate_direct_stars(
    scope: Scope,
    source_columns: dict[str, frozenset[str]],
) -> None:
    for projection in scope.expression.expressions:
        projected = projection.this if isinstance(projection, exp.Alias) else projection
        if isinstance(projected, exp.Star) and len(source_columns) != 1:
            raise SqlPolicyViolation(
                "star_ambiguous",
                "Unqualified star requires one source",
            )


_RAW_IDENTITY_COLUMNS = frozenset(
    {"seller_id", "customer_id", "customer_unique_id", "order_id"}
)


def _validate_identity_projections(
    scope: Scope,
    source_columns: dict[str, frozenset[str]],
) -> None:
    for projection in scope.expression.expressions:
        projected = projection.this if isinstance(projection, exp.Alias) else projection
        if isinstance(projected, exp.Column) and projected.name in _RAW_IDENTITY_COLUMNS:
            raise SqlPolicyViolation(
                "identity_projection_denied", "Raw identity projection is not allowed"
            )
        if isinstance(projected, exp.Star) and any(
            columns & _RAW_IDENTITY_COLUMNS for columns in source_columns.values()
        ):
            raise SqlPolicyViolation(
                "identity_projection_denied", "Raw identity star projection is not allowed"
            )
        if isinstance(projected, exp.Column) and projected.is_star:
            columns = source_columns.get(projected.table, frozenset())
            if columns & _RAW_IDENTITY_COLUMNS:
                raise SqlPolicyViolation(
                    "identity_projection_denied", "Raw identity star projection is not allowed"
                )
        if (
            isinstance(projected, exp.Column)
            and projected.is_star
            and projected.table not in source_columns
        ):
            raise SqlPolicyViolation(
                "star_denied",
                "Qualified star source is not allowed",
            )


def _validate_functions(statement: exp.Select) -> None:
    for function in statement.find_all(exp.Func):
        if function.sql_name().upper() not in ALLOWED_FUNCTIONS:
            raise SqlPolicyViolation("function_denied", "Function is not allowed")


def _and_terms(condition: exp.Expression) -> list[exp.Expression]:
    if isinstance(condition, exp.And):
        return _and_terms(condition.this) + _and_terms(condition.expression)
    return [condition]


def _column_ref(
    column: exp.Expression,
    source_tables: dict[str, str],
) -> tuple[str, str] | None:
    if not isinstance(column, exp.Column) or not column.table:
        return None
    table_name = source_tables.get(column.table)
    if table_name is None:
        return None
    return table_name, column.name


def _validate_joins(scope: Scope, source_tables: dict[str, str]) -> None:
    for join in scope.expression.args.get("joins") or []:
        if (join.args.get("kind") or "").upper() == "CROSS":
            raise SqlPolicyViolation("cross_join", "CROSS JOIN is not allowed")
        condition = join.args.get("on")
        if condition is None:
            raise SqlPolicyViolation("join_without_on", "JOIN requires ON")
        terms = _and_terms(condition)
        for term in terms:
            if not isinstance(term, exp.EQ):
                raise SqlPolicyViolation(
                    "join_condition",
                    "JOIN condition is not reviewed",
                )
            left = _column_ref(term.this, source_tables)
            right = _column_ref(term.expression, source_tables)
            edge = frozenset({left, right}) if left is not None and right is not None else None
            if edge not in ALLOWED_JOIN_EDGES:
                raise SqlPolicyViolation("join_edge", "JOIN edge is not reviewed")


def _validate_limit(statement: exp.Select, is_aggregate: bool) -> None:
    if is_aggregate:
        return
    limit = statement.args.get("limit")
    limit_value = limit.expression if limit is not None else None
    if not isinstance(limit_value, exp.Literal) or not limit_value.is_int:
        raise SqlPolicyViolation(
            "detail_limit",
            "Detail query requires an integer LIMIT",
        )
    parsed_limit = int(limit_value.this)
    if not 1 <= parsed_limit <= 1000:
        raise SqlPolicyViolation(
            "detail_limit",
            "Detail LIMIT must be between 1 and 1000",
        )


class AstPolicy:
    def validate(self, sql: str) -> ValidatedQuery:
        try:
            tokens = Tokenizer(dialect="postgres").tokenize(sql)
            if any(token.token_type is TokenType.SEMICOLON for token in tokens):
                raise SqlPolicyViolation(
                    "multiple_statements",
                    "Semicolons are not allowed",
                )
            statements = parse(
                sql,
                read="postgres",
                error_level=ErrorLevel.RAISE,
            )
        except ParseError as error:
            raise SqlPolicyViolation(
                "parse_error",
                "SQL could not be parsed",
            ) from error

        if len(statements) != 1 or statements[0] is None:
            raise SqlPolicyViolation(
                "statement_count",
                "Exactly one statement is required",
            )

        statement = statements[0]
        if not isinstance(statement, exp.Select):
            raise SqlPolicyViolation(
                "root_not_select",
                "Only SELECT is allowed",
            )

        denied_nodes = (
            exp.Delete,
            exp.Insert,
            exp.Update,
            exp.Merge,
            exp.Into,
            exp.Lock,
        )
        for denied_type in denied_nodes:
            if statement.find(denied_type) is not None:
                raise SqlPolicyViolation(
                    "denied_ast_node",
                    denied_type.__name__,
                )

        with_expression = statement.args.get("with_")
        if with_expression is not None and with_expression.args.get("recursive"):
            raise SqlPolicyViolation(
                "recursive_cte",
                "Recursive CTE is not allowed",
            )

        is_aggregate = (
            statement.find(exp.AggFunc) is not None or statement.args.get("group") is not None
        )
        for scope in traverse_scope(statement):
            source_columns, source_tables = _scope_sources(scope)
            _validate_scope_columns(scope, source_columns)
            _validate_direct_stars(scope, source_columns)
            _validate_joins(scope, source_tables)
            _validate_identity_projections(scope, source_columns)
        _validate_functions(statement)
        _validate_limit(statement, is_aggregate)
        return ValidatedQuery(sql=sql, is_aggregate=is_aggregate)
