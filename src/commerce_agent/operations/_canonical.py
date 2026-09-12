"""Versioned canonical projections used by operation signatures."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from commerce_agent.operations.commands import OperationCommand
from commerce_agent.operations.errors import OperationContractError


class CanonicalProjectable(Protocol):
    def canonical_projection(self) -> Mapping[str, object]: ...


class CanonicalPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int
    canonical_bytes: bytes
    sha256: str


def utc_z(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OperationContractError("datetime_timezone_required", retryable=False)
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalized_decimal(value: Decimal, *, scale: int) -> str:
    if not value.is_finite():
        raise OperationContractError("decimal_not_finite", retryable=False)
    try:
        quantized = value.quantize(Decimal(1).scaleb(-scale))
    except InvalidOperation as exc:
        raise OperationContractError("decimal_scale_invalid", retryable=False) from exc
    return format(quantized, f".{scale}f")


def _canonical_value(value: object) -> object:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise OperationContractError("canonical_key_invalid", retryable=False)
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, datetime):
        return utc_z(value)
    if isinstance(value, UUID):
        return str(value).lower()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        raise OperationContractError("canonical_decimal_requires_scale", retryable=False)
    if isinstance(value, float):
        raise OperationContractError("canonical_float_forbidden", retryable=False)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise OperationContractError("canonical_type_unsupported", retryable=False)


def canonical_json_bytes(value: CanonicalProjectable | Mapping[str, object]) -> bytes:
    projection = value.canonical_projection() if hasattr(value, "canonical_projection") else value
    normalized = _canonical_value(projection)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


_COMMAND_FIELDS: dict[str, tuple[str, ...]] = {
    "open_seller_risk_case": (
        "type",
        "seller_ref",
        "observation_started_at",
        "observation_ended_at",
        "metric_ref",
        "numerator",
        "denominator",
        "observed",
        "threshold",
        "title",
        "priority",
        "evidence_refs",
        "expected_target_version",
    ),
    "create_investigation_task": (
        "type",
        "title",
        "priority",
        "public_summary",
        "evidence_refs",
        "expected_target_version",
    ),
    "create_investigation_from_alert_hit": (
        "type",
        "alert_hit_ref",
        "title",
        "priority",
        "evidence_refs",
        "expected_target_version",
    ),
    "create_and_enable_metric_alert_rule": (
        "type",
        "alert_backtest_ref",
        "metric_ref",
        "grain",
        "window",
        "comparator",
        "threshold",
        "minimum_denominator",
        "filter_refs",
        "evidence_refs",
        "expected_target_version",
    ),
    "assign_investigation": (
        "type",
        "task_ref",
        "assignee_ref",
        "evidence_refs",
        "expected_target_version",
    ),
    "transition_investigation": (
        "type",
        "task_ref",
        "from_status",
        "to_status",
        "reason_code",
        "evidence_refs",
        "expected_target_version",
    ),
    "add_investigation_conclusion": (
        "type",
        "task_ref",
        "conclusion_code",
        "conclusion_summary",
        "evidence_refs",
        "expected_target_version",
    ),
    "close_investigation": (
        "type",
        "task_ref",
        "conclusion_ref",
        "risk_disposition",
        "evidence_refs",
        "expected_target_version",
    ),
}


def _project_model(value: Any) -> object:
    if isinstance(value, BaseModel):
        return {
            name: _project_model(getattr(value, name))
            for name in type(value).model_fields
        }
    if isinstance(value, tuple):
        return [_project_model(item) for item in value]
    if isinstance(value, Decimal):
        return normalized_decimal(value, scale=4)
    return value


def canonical_command(
    command: OperationCommand,
    target_versions: Mapping[str, int],
    *,
    schema_version: int = 1,
) -> CanonicalPayload:
    fields = _COMMAND_FIELDS[command.type]
    command_projection = {name: _project_model(getattr(command, name)) for name in fields}
    canonical = canonical_json_bytes(
        {
            "command": command_projection,
            "schema_version": schema_version,
            "target_versions": dict(target_versions),
        }
    )
    return CanonicalPayload(
        schema_version=schema_version,
        canonical_bytes=canonical,
        sha256=hashlib.sha256(canonical).hexdigest(),
    )
