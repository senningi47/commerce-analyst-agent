from commerce_agent.sql_reasoning.reasoner import fingerprints


def test_literal_change_keeps_structure_and_changes_execution() -> None:
    first = fingerprints(
        "SELECT COUNT(*) FROM retail.orders WHERE order_status = 'delivered'"
    )
    second = fingerprints(
        "SELECT COUNT(*) FROM retail.orders WHERE order_status = 'canceled'"
    )

    assert first[0] == second[0]
    assert first[1] != second[1]


def test_identifier_change_changes_both_fingerprints() -> None:
    first = fingerprints("SELECT COUNT(*) FROM retail.orders")
    second = fingerprints("SELECT COUNT(*) FROM retail.order_items")

    assert first[0] != second[0]
    assert first[1] != second[1]
