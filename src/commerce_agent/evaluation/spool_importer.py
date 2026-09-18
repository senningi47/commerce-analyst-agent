"""Spool usage importer: aggregate agent spool turns and bind them to eval attempts.

Day 6 wiring for the §18 agent-visible spool. The agent-side gateway stamps
every model-turn event with `task_id`/`mode`/`experiment_id`/`recorded_at`;
this module scans a spool directory, aggregates usage/cost per agent session,
and assigns each session to an `eval.task_attempt` row by
`(experiment_id, task_id, mode)` plus a started/finished time window when a
task was retried. Legacy spool files (before the Day 6 stamping) cannot be
bound and are skipped with a count — their totals already live in the pilot
ledger, not here. Assignment is deliberately conservative: anything ambiguous
stays unassigned and is reported, never guessed.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field

from commerce_agent.evaluation.spool import SpoolValidationError, _reject_forbidden_keys

_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "cache_hit_tokens",
    "cache_miss_tokens",
    "total_tokens",
)
_STAMP_KEYS = ("task_id", "mode", "experiment_id")  # per-file invariants; recorded_at varies
_TIME_KEYS = ("first_recorded_at", "last_recorded_at")


class SpoolImportError(RuntimeError):
    pass


class SpoolUsageRecord(BaseModel, frozen=True):
    """Aggregated agent-side usage for one agent session (one spool file)."""

    attempt_id: UUID
    experiment_id: str
    task_id: str
    mode: str
    turns: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    cache_hit_tokens: int = Field(ge=0)
    cache_miss_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost_usd: Decimal = Field(ge=0)
    price_snapshot_id: str = Field(min_length=1, max_length=128)
    price_band: str
    first_recorded_at: datetime | None = None
    last_recorded_at: datetime | None = None


class SpoolScanReport(BaseModel, frozen=True):
    records: tuple[SpoolUsageRecord, ...] = ()
    files_scanned: int = Field(ge=0)
    files_skipped: int = Field(ge=0)


class AttemptRow(BaseModel, frozen=True):
    attempt_id: UUID
    experiment_id: str
    task_id: str
    mode: str
    started_at: datetime
    finished_at: datetime | None


class SpoolAssignment(BaseModel, frozen=True):
    assigned: dict[UUID, SpoolUsageRecord]
    unassigned: tuple[SpoolUsageRecord, ...]
    ambiguous: tuple[SpoolUsageRecord, ...]


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _usable_turn(event: dict[str, object]) -> bool:
    if any(key not in event for key in _STAMP_KEYS) or "recorded_at" not in event:
        return False
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return False
    usage = payload.get("usage")
    cost = payload.get("cost")
    if not isinstance(usage, dict) or not isinstance(cost, dict):
        return False
    if any(key not in usage for key in _USAGE_KEYS):
        return False
    amount = cost.get("amount")
    if not isinstance(amount, (str, int, float)) or isinstance(amount, bool):
        return False
    try:
        Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return False
    return True


def _scan_file(path: Path) -> SpoolUsageRecord | None:
    """Aggregate one spool file; None when the file yields no usable session.

    Fail-closed per file: a single line that violates the event schema
    (unreadable JSON, forbidden key, missing fields, sequence regression,
    mid-file identity change) poisons the whole file — a spool file must be
    one clean session or it cannot be trusted as an aggregation unit.
    """
    total_tokens = {key: 0 for key in _USAGE_KEYS}
    turns = 0
    cost_total = Decimal(0)
    cost_meta: tuple[str, str] | None = None
    first_at: datetime | None = None
    last_at: datetime | None = None
    attempt_id: UUID | None = None
    stamp: dict[str, str] = {}
    last_sequence: int | None = None

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(event, dict):
            return None
        try:
            _reject_forbidden_keys(event)
        except SpoolValidationError:
            return None
        sequence = event.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool):
            return None
        if last_sequence is not None and sequence <= last_sequence:
            return None
        last_sequence = sequence
        if not _usable_turn(event):
            return None
        if event["event_type"] != "model_turn":
            continue

        file_attempt = event.get("attempt_id")
        if not isinstance(file_attempt, str):
            return None
        try:
            parsed_attempt = UUID(file_attempt)
        except ValueError:
            return None
        if attempt_id is None:
            attempt_id = parsed_attempt
            stamp = {key: str(event[key]) for key in _STAMP_KEYS}
        elif parsed_attempt != attempt_id or any(
            str(event[key]) != stamp[key] for key in _STAMP_KEYS
        ):
            return None

        payload = event["payload"]
        assert isinstance(payload, dict)
        usage = payload["usage"]
        assert isinstance(usage, dict)
        for key in _USAGE_KEYS:
            value = usage[key]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return None
            total_tokens[key] += value
        cost_payload = payload["cost"]
        assert isinstance(cost_payload, dict)
        amount = cost_payload.get("amount")
        try:
            cost_total += Decimal(str(amount))
        except (InvalidOperation, ValueError):
            return None
        snapshot_id = cost_payload.get("price_snapshot_id")
        band = cost_payload.get("price_band")
        if not isinstance(snapshot_id, str) or not snapshot_id or band not in ("peak", "off_peak"):
            return None
        if cost_meta is None:
            cost_meta = (snapshot_id, band)
        elif cost_meta != (snapshot_id, band):
            return None  # mixed pricing within one session: not a coherent aggregate
        recorded_at = _parse_timestamp(event["recorded_at"])
        if recorded_at is None:
            return None
        if first_at is None or recorded_at < first_at:
            first_at = recorded_at
        if last_at is None or recorded_at > last_at:
            last_at = recorded_at
        turns += 1

    if attempt_id is None or turns == 0:
        return None
    assert cost_meta is not None
    return SpoolUsageRecord(
        attempt_id=attempt_id,
        experiment_id=stamp["experiment_id"],
        task_id=stamp["task_id"],
        mode=stamp["mode"],
        turns=turns,
        cost_usd=cost_total,
        price_snapshot_id=cost_meta[0],
        price_band=cost_meta[1],
        first_recorded_at=first_at,
        last_recorded_at=last_at,
        **total_tokens,
    )


def scan_spool_dir(spool_dir: Path) -> SpoolScanReport:
    if not spool_dir.is_dir():
        raise SpoolImportError("spool_dir_missing")
    records: list[SpoolUsageRecord] = []
    files_scanned = 0
    files_skipped = 0
    lines_skipped_invalid = 0
    for path in sorted(spool_dir.glob("*.jsonl")):
        files_scanned += 1
        record = _scan_file(path)
        if record is None:
            files_skipped += 1
            continue
        records.append(record)
    return SpoolScanReport(
        records=tuple(records),
        files_scanned=files_scanned,
        files_skipped=files_skipped,
        lines_skipped_invalid=lines_skipped_invalid,
    )


def _in_window(
    record: SpoolUsageRecord,
    attempt: AttemptRow,
    grace: timedelta,
) -> bool:
    observed = record.last_recorded_at or record.first_recorded_at
    if observed is None:
        return True  # group already unique; no window to violate
    lower = attempt.started_at - grace
    upper = (attempt.finished_at or attempt.started_at) + grace
    return lower <= observed <= upper


def assign_attempts(
    records: list[SpoolUsageRecord] | tuple[SpoolUsageRecord, ...],
    attempts: list[AttemptRow] | tuple[AttemptRow, ...],
    *,
    clock_grace: timedelta = timedelta(seconds=60),
) -> SpoolAssignment:
    groups: dict[tuple[str, str, str], list[SpoolUsageRecord]] = defaultdict(list)
    for record in records:
        groups[(record.experiment_id, record.task_id, record.mode)].append(record)
    attempt_groups: dict[tuple[str, str, str], list[AttemptRow]] = defaultdict(list)
    for attempt in attempts:
        attempt_groups[(attempt.experiment_id, attempt.task_id, attempt.mode)].append(attempt)

    assigned: dict[UUID, SpoolUsageRecord] = {}
    unassigned: list[SpoolUsageRecord] = []
    ambiguous: list[SpoolUsageRecord] = []
    for key, group in groups.items():
        candidates = attempt_groups.get(key, ())
        if len(group) == 1 and len(candidates) == 1:
            # codex F9: the unique-candidate shortcut must still honor the
            # time window — a far-out-of-window session stays unassigned
            # rather than being claimed by an unrelated attempt
            if _in_window(group[0], candidates[0], clock_grace):
                assigned[candidates[0].attempt_id] = group[0]
            else:
                unassigned.append(group[0])
            continue
        if not candidates:
            unassigned.extend(group)
            continue
        for record in group:
            hits = [
                attempt
                for attempt in candidates
                if attempt.attempt_id not in assigned and _in_window(record, attempt, clock_grace)
            ]
            if len(hits) == 1:
                assigned[hits[0].attempt_id] = record
            elif len(hits) == 0:
                unassigned.append(record)
            else:
                ambiguous.append(record)
    return SpoolAssignment(
        assigned=assigned,
        unassigned=tuple(unassigned),
        ambiguous=tuple(ambiguous),
    )


def telemetry_patch(record: SpoolUsageRecord) -> dict[str, object]:
    """Build the jsonb patch merged into `eval.task_attempt.telemetry`.

    Shaped to the `AttemptTelemetry` contract: `agent_usage` satisfies the
    reported-token identities (aggregation preserves them) and `agent_cost`
    is an estimated-cost object under the session's single price snapshot.
    """
    return {
        "agent_cost": {
            "status": "estimated",
            "price_snapshot_id": record.price_snapshot_id,
            "currency": "USD",
            "amount": str(record.cost_usd),
            "price_band": record.price_band,
        },
        "agent_usage": {
            "status": "reported",
            "prompt_tokens": record.prompt_tokens,
            "completion_tokens": record.completion_tokens,
            "reasoning_tokens": record.reasoning_tokens,
            "cache_hit_tokens": record.cache_hit_tokens,
            "cache_miss_tokens": record.cache_miss_tokens,
            "total_tokens": record.total_tokens,
        },
        "agent_turns": record.turns,
        "agent_session_id": str(record.attempt_id),
    }
