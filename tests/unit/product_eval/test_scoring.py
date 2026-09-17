"""Unit tests for §17.1 scoring normalization."""

from decimal import Decimal

from commerce_agent.product_eval.scoring import (
    normalize_rows,
    result_digest,
    results_match,
)


def test_normalization_is_column_label_free_and_order_independent() -> None:
    left = [{"total": Decimal("10.50"), "month": "2018-01"}]
    right = [{"month": "2018-01", "total": 10.5}]
    assert results_match(left, right)


def test_decimal_quantization_absorbs_float_noise() -> None:
    a = result_digest([{"v": 0.1 + 0.2}])
    b = result_digest([{"v": Decimal("0.3")}])
    assert a == b


def test_row_multiset_ignores_ordering() -> None:
    left = [{"v": 1}, {"v": 2}]
    right = [{"v": 2}, {"v": 1}]
    assert normalize_rows(left) == normalize_rows(right)
    assert results_match(left, right)


def test_distinct_results_differ() -> None:
    assert not results_match([{"v": 1}], [{"v": 2}])


def test_none_and_strings_survive_round_trip() -> None:
    rows = [{"s": "abc", "n": None, "b": True}]
    assert results_match(rows, rows)
