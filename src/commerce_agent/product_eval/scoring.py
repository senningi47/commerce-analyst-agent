"""Deterministic scoring for the §17.1 product question evaluation.

Contract (post codex-review F1/F2, 2026-09-18):

- Column alignment is a CONSISTENT permutation between the agent columns
  and the reference columns, inferred per question: every row must match
  under the same mapping (codex F2), and the agent may alias/reorder freely.
- Cells are compared positionally within the aligned column order — a row is
  a tuple of values, never a bag of cells (codex F2).
- Scalar typing is anchored on the reference side: where the reference value
  is numeric (Decimal/int/float), the agent value is parsed as a number and
  quantized to 6 dp; where the reference is text, the agent value must be
  equal text. This kills the Decimal-vs-str false negative at the
  QueryEngine boundary (codex F1) without blind string→number coercion.
- Rows form a multiset by default; questions that declare an ordering
  (`row_order="ascending"`) compare sequences instead.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from itertools import pairwise, permutations
from typing import Any

_QUANT = Decimal("0.000001")


def _numeric(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _quantize_pair(reference: Any, actual: Any) -> tuple[str, str] | None:
    """Normalize one (reference, actual) cell pair; None when incomparable."""
    if reference is None or actual is None:
        return (None, None) if reference is None and actual is None else None
    if isinstance(reference, bool) or isinstance(actual, bool):
        return (str(reference), str(actual)) if str(reference) == str(actual) else None
    if isinstance(reference, (int, float, Decimal)):
        reference_number = _numeric(reference)
        actual_number = _numeric(actual)
        if reference_number is None or actual_number is None:
            return None
        return (
            format(reference_number.quantize(_QUANT), "f"),
            format(actual_number.quantize(_QUANT), "f"),
        )
    reference_text = str(reference)
    actual_text = str(actual)
    return (reference_text, actual_text) if reference_text == actual_text else None


def canonical_reference_rows(
    rows: list[dict[str, Any]], columns: list[str] | tuple[str, ...]
) -> list[list[Any]]:
    """Reference rows as positional cell lists (digest form)."""
    return [[row[column] for column in columns] for row in rows]


def results_match(
    agent_rows: list[dict[str, Any]],
    reference_rows: list[dict[str, Any]],
    reference_columns: list[str] | tuple[str, ...],
    *,
    ordered: bool = False,
) -> bool:
    """Compare agent results against reference results.

    ``reference_rows`` cells are positional per ``reference_columns``; agent
    rows (dicts keyed by the agent's own column labels) are aligned to the
    reference columns by name.
    """
    if not reference_rows:
        return not agent_rows
    if not agent_rows:
        return False
    if len(reference_rows) != len(agent_rows):
        return False
    reference_positional = canonical_reference_rows(reference_rows, reference_columns)
    agent_positional = [list(row.values()) for row in agent_rows]
    width = len(reference_columns)
    if any(len(cells) != width for cells in agent_positional):
        return False
    # one consistent column mapping for ALL rows (codex F2): try every
    # permutation of the agent columns against the reference order
    for permutation in permutations(range(width)):
        candidate = [
            [agent_cells[position] for position in permutation]
            for agent_cells in agent_positional
        ]
        normalized: list[list[tuple[str, str]]] = []
        matched = True
        for reference_row, agent_row in zip(
            reference_positional, candidate, strict=True
        ):
            row_pairs: list[tuple[str, str]] = []
            for reference_cell, agent_cell in zip(reference_row, agent_row, strict=True):
                pair = _quantize_pair(reference_cell, agent_cell)
                if pair is None:
                    matched = False
                    break
                row_pairs.append(pair)
            if not matched:
                break
            normalized.append(row_pairs)
        if matched:
            if ordered:
                if all(
                    left == right
                    for left, right in pairwise(normalized)
                ):
                    return True
            else:
                reference_sorted = sorted(
                    json.dumps([pair[0] for pair in row], ensure_ascii=False)
                    for row in normalized
                )
                actual_sorted = sorted(
                    json.dumps([pair[1] for pair in row], ensure_ascii=False)
                    for row in normalized
                )
                if reference_sorted == actual_sorted:
                    return True
    return False
    if ordered:
        return all(left == right for left, right in pairwise(normalized))
    reference_sorted = sorted(
        json.dumps([pair[0] for pair in row], ensure_ascii=False) for row in normalized
    )
    actual_sorted = sorted(
        json.dumps([pair[1] for pair in row], ensure_ascii=False) for row in normalized
    )
    return reference_sorted == actual_sorted


def result_digest(rows: list[dict[str, Any]]) -> str:
    """Integrity digest for reference rows (bank storage form): positional
    cells in cursor column order, numerics quantized to 6 dp, row multiset."""
    normalized: list[list[str]] = []
    for row in rows:
        cells: list[str] = []
        for value in row.values():
            if isinstance(value, Decimal):
                cells.append(format(value.quantize(_QUANT), "f"))
            elif isinstance(value, (int, float)):
                number = _numeric(value)
                cells.append(format(number.quantize(_QUANT), "f") if number else str(value))
            elif value is None:
                cells.append("null")
            else:
                cells.append(str(value))
        normalized.append(cells)
    normalized.sort(key=lambda cells: json.dumps(cells, ensure_ascii=False))
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()
