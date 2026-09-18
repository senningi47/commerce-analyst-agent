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


def test_ordered_compares_reference_row_i_against_agent_row_i() -> None:
    """Codex round-2 P1-1 regression: the ordered branch compared ADJACENT
    agent rows (single-row pairwise is vacuous — any single row matched)."""
    gold_rows = [{"v": 1}, {"v": 2}]
    assert results_match([{"v": 1}, {"v": 2}], gold_rows, ["v"], ordered=True)
    assert not results_match([{"v": 999}], [{"v": 1}], ["v"], ordered=True)
    assert not results_match(
        [{"v": 999}, {"v": 999}], [{"v": 1}, {"v": 1}], ["v"], ordered=True
    )
    assert not results_match([{"v": 2}, {"v": 1}], gold_rows, ["v"], ordered=True)


def test_ordered_multi_row_identical_rows_match() -> None:
    gold_rows = [{"v": 1}, {"v": 1}]
    assert results_match([{"v": 1}, {"v": 1}], gold_rows, ["v"], ordered=True)


def test_unordered_matches_multiset_after_independent_normalization() -> None:
    """Codex round-2 P1-2 regression: rows were paired positionally BEFORE
    normalization, so reordered text/NULL results failed and unordered
    comparison silently depended on database row order."""
    gold_rows = [{"k": "B", "v": 2}, {"k": "A", "v": 1}]
    agent_rows = [{"k": "A", "v": 1}, {"k": "B", "v": 2}]
    assert results_match(agent_rows, gold_rows, ["k", "v"])
    gold_nulls = [{"k": "B", "v": None}, {"k": "A", "v": 1}]
    agent_nulls = [{"k": "A", "v": 1}, {"k": "B", "v": None}]
    assert results_match(agent_nulls, gold_nulls, ["k", "v"])
    assert results_match(
        [{"v": 2}, {"v": 1}], [{"v": 1}, {"v": 2}], ["v"]
    )


def test_unordered_duplicate_row_count_matters() -> None:
    gold_rows = [{"k": "A", "v": 1}, {"k": "A", "v": 1}]
    assert results_match([{"k": "A", "v": 1}, {"k": "A", "v": 1}], gold_rows, ["k", "v"])
    assert not results_match([{"k": "A", "v": 1}], gold_rows, ["k", "v"])


def test_unordered_wrong_text_value_is_rejected() -> None:
    gold_rows = [{"k": "B", "v": 2}, {"k": "A", "v": 1}]
    assert not results_match(
        [{"k": "A", "v": 1}, {"k": "C", "v": 2}], gold_rows, ["k", "v"]
    )


def test_single_row_column_swap_is_disclosed_value_match() -> None:
    """Disclosed ambiguity (codex round-2 §2): with one row, a swapped value
    pattern is indistinguishable from a legitimate column reorder — labels
    are non-semantic by the bank contract."""
    gold_rows = [{"revenue": 100, "cost": 10}]
    assert results_match([{"revenue": 10, "cost": 100}], gold_rows, ["revenue", "cost"])


def test_mixed_kind_reference_column_fails_closed() -> None:
    """A reference column mixing non-null numeric and text cannot come from
    one homogeneous SQL column — refuse to guess rather than mis-score."""
    gold_rows = [{"x": 1}, {"x": "a"}]
    assert not results_match([{"x": 1}, {"x": "a"}], gold_rows, ["x"])


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
