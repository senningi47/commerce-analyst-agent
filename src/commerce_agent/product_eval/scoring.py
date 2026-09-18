"""Deterministic scoring for the §17.1 product question evaluation.

Contract (post codex-review F1/F2, round 2, 2026-09-18):

- Column alignment is a CONSISTENT permutation between the agent columns
  and the reference columns, inferred per question: every row must match
  under the same mapping (codex F2), and the agent may alias/reorder freely.
- Cells are compared positionally within the aligned column order — a row is
  a tuple of values, never a bag of cells (codex F2). Each side is
  normalized independently per column BEFORE rows are paired, so a row
  multiset comparison never depends on database row order (codex round-2
  P1-2): the column's type is anchored on the reference side (numeric →
  both cells parsed as numbers and quantized to 6 dp; bool → str; text →
  str), not on the incidental row pairing.
- Ordered questions compare normalized rows positionally (reference row i
  against agent row i, cell by cell — codex round-2 P1-1); unordered
  questions compare row multisets.
- Scalar typing is anchored on the reference side (codex F1): a numeric
  reference column accepts the agent value as a number regardless of the
  QueryEngine's string boundary; a text reference column requires equal
  text. A reference column mixing non-null numeric and text values fails
  closed (cannot happen from a single homogeneous SQL column).

Known disclosed limitation (codex round-2 §2): with a single row, a value
pattern that differs from the reference only by a swapped column mapping is
indistinguishable from a legitimate column reorder — matching is by value
with non-semantic labels, not by column role. The bank contract declares
labels non-semantic; semantic-role scoring would require per-question field
role metadata that the bank does not carry.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from itertools import permutations
from typing import Any

_QUANT = Decimal("0.000001")


def _numeric(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _cell_kind(value: Any) -> str:
    if isinstance(value, bool):  # bool subclasses int — test first
        return "bool"
    if isinstance(value, (int, float, Decimal)):
        return "number"
    return "text"


def _reference_column_kinds(
    reference_positional: list[list[Any]], width: int
) -> list[str] | None:
    """Per-column type anchored on the reference side; None fails closed."""
    kinds: list[str] = []
    for index in range(width):
        kind: str | None = None
        for row in reference_positional:
            value = row[index]
            if value is None:
                continue
            current = _cell_kind(value)
            if kind is None:
                kind = current
            elif kind != current:
                return None
        kinds.append(kind or "text")
    return kinds


def _normalize_row(
    cells: list[Any], kinds: list[str]
) -> list[str | None] | None:
    """Normalize one row under per-column kinds; None when unnormalizable."""
    normalized: list[str | None] = []
    for value, kind in zip(cells, kinds, strict=True):
        if value is None:
            normalized.append(None)
        elif kind == "number":
            number = _numeric(value)
            if number is None:
                return None
            normalized.append(format(number.quantize(_QUANT), "f"))
        elif kind == "bool":
            normalized.append(str(bool(value)))
        else:
            normalized.append(str(value))
    return normalized


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
    reference columns by one consistent permutation. Both sides are
    normalized per column before rows are paired, so unordered comparison
    is a true row multiset comparison and ordered comparison pairs
    reference row ``i`` with agent row ``i``.
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
    kinds = _reference_column_kinds(reference_positional, width)
    if kinds is None:
        return False
    normalized_reference: list[list[str | None]] = []
    for row in reference_positional:
        normalized = _normalize_row(row, kinds)
        if normalized is None:
            return False
        normalized_reference.append(normalized)
    # one consistent column mapping for ALL rows (codex F2): try every
    # permutation of the agent columns against the reference order
    for permutation in permutations(range(width)):
        candidate = [
            [agent_cells[position] for position in permutation]
            for agent_cells in agent_positional
        ]
        normalized_agent: list[list[str | None]] = []
        for row in candidate:
            normalized = _normalize_row(row, kinds)
            if normalized is None:
                break
            normalized_agent.append(normalized)
        if len(normalized_agent) != len(reference_rows):
            continue
        if ordered:
            if normalized_reference == normalized_agent:
                return True
        else:
            reference_sorted = sorted(
                json.dumps(row, ensure_ascii=False) for row in normalized_reference
            )
            actual_sorted = sorted(
                json.dumps(row, ensure_ascii=False) for row in normalized_agent
            )
            if reference_sorted == actual_sorted:
                return True
    return False


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
