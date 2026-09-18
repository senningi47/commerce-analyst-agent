"""Unit tests for the §17.1 scoring contract (post codex-review F1/F2)."""

from decimal import Decimal

from commerce_agent.product_eval.scoring import result_digest, results_match


def test_numeric_decimal_vs_str_matches_anchored_on_reference() -> None:
    """Codex F1 regression: the QueryEngine stringifies Decimal while the
    reference path keeps Decimal — a numeric reference anchors the parse."""
    gold_rows = [{"total": Decimal("10.50")}]
    agent_rows = [{"total": "10.500000"}]
    assert results_match(agent_rows, gold_rows, ["total"])


def test_consistent_column_permutation_matches() -> None:
    """The agent may alias/reorder columns, but the mapping must be the same
    for every row (alias-order freedom, alias-name freedom)."""
    gold_rows = [{"month": "2018-01", "total": Decimal(10)}]
    agent_rows = [{"total": "10.000000", "month": "2018-01"}]
    assert results_match(agent_rows, gold_rows, ["month", "total"])


def test_codex_f2_row_cell_bagging_is_rejected() -> None:
    """A different column mapping per row cannot match — the permutation is
    required to be consistent across all rows (codex F2)."""
    gold_rows = [{"a": 1, "b": 10}, {"a": 20, "b": 2}]
    agent_rows = [{"a": 1, "b": 10}, {"a": 2, "b": 20}]
    assert not results_match(agent_rows, gold_rows, ["a", "b"])


def test_ordered_contract_rejects_reversed_time_series() -> None:
    gold_rows = [{"y": 2017}, {"y": 2018}]
    agent_rows = [{"y": 2018}, {"y": 2017}]
    assert not results_match(agent_rows, gold_rows, ["y"], ordered=True)
    assert results_match(agent_rows, gold_rows, ["y"], ordered=False)


def test_row_count_mismatch_is_incorrect() -> None:
    gold_rows = [{"v": 1}, {"v": 2}]
    assert not results_match([{"v": 1}], gold_rows, ["v"])


def test_text_reference_requires_equal_text() -> None:
    gold_rows = [{"note": "abc"}]
    assert results_match([{"note": "abc"}], gold_rows, ["note"])
    assert not results_match([{"note": "abd"}], gold_rows, ["note"])


def test_empty_reference_needs_empty_agent() -> None:
    assert results_match([], [], ["v"])
    assert not results_match([{"v": 1}], [], ["v"])


def test_result_digest_stable_multiset_form() -> None:
    left = result_digest([{"v": Decimal("1.20")}, {"v": None}])
    right = result_digest([{"v": None}, {"v": "1.200000"}])
    assert left == right
