import pytest
from sqlglot import exp, parse_one
from sqlglot.tokens import Tokenizer, TokenType

from commerce_agent.query_engine._ast_policy import AstPolicy
from commerce_agent.query_engine.errors import SqlPolicyViolation


@pytest.fixture
def policy() -> AstPolicy:
    return AstPolicy()


def test_allows_one_bounded_select(policy: AstPolicy) -> None:
    query = policy.validate("SELECT order_status FROM retail.orders LIMIT 10")

    assert query.sql == "SELECT order_status FROM retail.orders LIMIT 10"
    assert query.is_aggregate is False


def test_allows_a_non_recursive_read_only_cte(policy: AstPolicy) -> None:
    query = policy.validate(
        "WITH recent AS ("
        "SELECT order_status FROM retail.orders"
        ") SELECT r.order_status FROM recent AS r LIMIT 10"
    )

    assert query.is_aggregate is False


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; SELECT 2",
        "SELECT 1;",
        "UPDATE retail.orders SET order_status = 'x'",
        "WITH changed AS (DELETE FROM retail.orders RETURNING *) SELECT * FROM changed",
        "WITH RECURSIVE n AS (SELECT 1 UNION ALL SELECT 1) SELECT * FROM n",
        "SELECT order_id INTO copied_orders FROM retail.orders",
        "SELECT order_id FROM retail.orders FOR UPDATE",
    ],
)
def test_rejects_unsafe_statement_shapes(policy: AstPolicy, sql: str) -> None:
    with pytest.raises(SqlPolicyViolation):
        policy.validate(sql)


def test_sqlglot_exposes_denied_ast_nodes() -> None:
    delete_cte = parse_one(
        "WITH changed AS (DELETE FROM retail.orders RETURNING *) SELECT * FROM changed",
        read="postgres",
    )
    into_select = parse_one(
        "SELECT order_id INTO copied_orders FROM retail.orders",
        read="postgres",
    )
    locked_select = parse_one(
        "SELECT order_id FROM retail.orders FOR UPDATE",
        read="postgres",
    )

    assert delete_cte.find(exp.Delete) is not None
    assert into_select.find(exp.Into) is not None
    assert locked_select.find(exp.Lock) is not None


def test_sqlglot_marks_recursive_ctes() -> None:
    statement = parse_one(
        "WITH RECURSIVE n AS (SELECT 1 UNION ALL SELECT 1) SELECT * FROM n",
        read="postgres",
    )
    with_expression = statement.args["with_"]

    assert with_expression.args["recursive"] is True


def test_tokenizer_distinguishes_structural_semicolons() -> None:
    tokenizer = Tokenizer(dialect="postgres")

    structural = tokenizer.tokenize("SELECT 1;")
    string_literal = tokenizer.tokenize("SELECT ';' AS value")

    assert any(token.token_type is TokenType.SEMICOLON for token in structural)
    assert all(token.token_type is not TokenType.SEMICOLON for token in string_literal)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM pg_catalog.pg_roles LIMIT 10",
        "SELECT * FROM information_schema.tables LIMIT 10",
        "SELECT missing_column FROM retail.orders LIMIT 10",
        "SELECT pg_sleep(1) FROM retail.orders LIMIT 10",
        "SELECT nextval('retail.some_sequence')",
    ],
)
def test_rejects_unknown_or_privileged_objects(
    policy: AstPolicy,
    sql: str,
) -> None:
    with pytest.raises(SqlPolicyViolation):
        policy.validate(sql)


def test_allows_reviewed_aggregate_functions(policy: AstPolicy) -> None:
    query = policy.validate(
        "SELECT order_status, COUNT(*) AS orders, SUM(price) AS amount "
        "FROM retail.orders o "
        "JOIN retail.order_items i ON i.order_id = o.order_id "
        "GROUP BY order_status"
    )

    assert query.is_aggregate is True


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM retail.orders",
        "SELECT * FROM retail.orders LIMIT 1001",
        ("SELECT o.order_id FROM retail.orders AS o CROSS JOIN retail.customers AS c LIMIT 10"),
        ("SELECT * FROM retail.orders o JOIN retail.products p ON true LIMIT 10"),
        (
            "SELECT * FROM retail.orders o "
            "JOIN retail.products p ON o.order_id = p.product_id LIMIT 10"
        ),
        (
            "SELECT * FROM retail.orders o "
            "JOIN retail.order_items i ON i.order_id = o.order_id LIMIT 10"
        ),
    ],
)
def test_rejects_unbounded_or_unknown_joins(
    policy: AstPolicy,
    sql: str,
) -> None:
    with pytest.raises(SqlPolicyViolation):
        policy.validate(sql)


def test_cross_join_has_a_stable_reason_code(policy: AstPolicy) -> None:
    with pytest.raises(SqlPolicyViolation) as captured:
        policy.validate(
            "SELECT o.order_id FROM retail.orders AS o CROSS JOIN retail.customers AS c LIMIT 10"
        )

    assert captured.value.reason_code == "cross_join"


def test_allows_only_reviewed_ops_read_views() -> None:
    policy = AstPolicy()

    assert policy.validate(
        "SELECT task_ref, status FROM ops_read.investigation_tasks LIMIT 10"
    )
    for sql in (
        "SELECT * FROM ops.operation_proposal LIMIT 10",
        "SELECT * FROM ops_read.operation_proposals LIMIT 10",
        "SELECT signature FROM ops_read.investigation_tasks LIMIT 10",
    ):
        with pytest.raises(SqlPolicyViolation):
            policy.validate(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT seller_id AS merchant FROM retail.sellers LIMIT 10",
        "SELECT customer_id AS buyer FROM retail.customers LIMIT 10",
        "SELECT order_id AS purchase FROM retail.orders LIMIT 10",
    ],
)
def test_direct_identity_projection_is_rejected_even_when_aliased(sql: str) -> None:
    with pytest.raises(SqlPolicyViolation) as caught:
        AstPolicy().validate(sql)
    assert caught.value.reason_code == "identity_projection_denied"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT (s.*) AS x FROM retail.sellers s LIMIT 1",
        "SELECT COALESCE(s.*, s.*) AS x FROM retail.sellers s LIMIT 1",
        "SELECT COUNT(s.*) FROM retail.sellers s",
    ],
)
def test_expression_wrapped_star_is_rejected(sql: str) -> None:
    """Codex round-2 P1-3: a nested qualified star expands to the source
    row's columns (including identities) and a composite value can carry
    them through result serialization as one opaque string — the name-based
    leak check cannot see it. Stars wrapped in expressions are denied
    outright; the root star keeps its dedicated checks."""
    with pytest.raises(SqlPolicyViolation) as caught:
        AstPolicy().validate(sql)
    assert caught.value.reason_code == "identity_projection_denied"


def test_root_star_on_identity_free_table_still_allowed() -> None:
    """The nested-star denial must not take out legitimate detail reads."""
    AstPolicy().validate("SELECT p.* FROM retail.products p LIMIT 3")
    AstPolicy().validate("SELECT * FROM retail.products LIMIT 3")


def test_allows_boolean_operators_in_filters(policy: AstPolicy) -> None:
    """2026-09-17 product-eval discovery: this sqlglot version subclasses
    boolean operators from Func, so the function allowlist rejected every
    AND/OR filter. Operators are not functions; multi-condition filters must
    stay expressible."""
    query = policy.validate(
        "SELECT COUNT(DISTINCT order_id) AS order_count FROM retail.orders "
        "WHERE order_purchase_timestamp >= '2016-01-01' "
        "AND order_purchase_timestamp < '2017-01-01'"
    )
    assert query.is_aggregate is True
    query = policy.validate(
        "SELECT COUNT(*) AS n FROM retail.orders "
        "WHERE order_status = 'delivered' OR order_status = 'shipped'"
    )
    assert query.is_aggregate is True
