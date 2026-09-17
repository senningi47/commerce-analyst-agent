"""Deterministic scoring for the §17.1 product question evaluation.

Gold and agent SQL results are compared as an unordered multiset of rows:
column labels are ignored (aliases may differ), every scalar is quantized to
6 decimal places (numeric money sums are exact in PostgreSQL; the tolerance
absorbs client float round-trips), and rows are sorted so digest equality is
order-independent. Gold Recall@5 scores how many of a question's gold tables
appear in the table documents supplied to the model.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any

_QUANT = Decimal("0.000001")


def _quantize(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Decimal):
        return format(value.quantize(_QUANT), "f")
    if isinstance(value, (int, float)):
        try:
            return format(Decimal(str(value)).quantize(_QUANT), "f")
        except (InvalidOperation, ValueError):
            return str(value)
    return str(value)


def normalize_rows(rows: list[dict[str, Any]]) -> list[list[Any]]:
    """Column-label-free, order-independent row normalization.

    Cells within a row are sorted too: with labels ignored, cell order is
    ambiguous, so a row is compared as a multiset of quantized values.
    """
    normalized = [
        sorted(
            (_quantize(value) for value in row.values()),
            key=lambda cell: json.dumps(cell, ensure_ascii=False),
        )
        for row in rows
    ]
    return sorted(normalized, key=lambda cells: json.dumps(cells, ensure_ascii=False))


def result_digest(rows: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        normalize_rows(rows), ensure_ascii=False, sort_keys=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def results_match(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    return result_digest(left) == result_digest(right)
