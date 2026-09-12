"""Agent-visible JSONL spool writer and the orchestrator-side import validator.

Trust boundary (v0.3 §18): the system-agent process may only append bounded
JSONL events to its own spool; the orchestrator validates the spool strictly
(schema, scope/attempt binding, per-phase sequence monotonicity, size cap,
evaluator-only key rejection) before archiving it read-only. The archive path
and digest are what later surface in `AttemptTelemetry`.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field

DEFAULT_MAX_BYTES = 33_554_432
FORBIDDEN_KEY_TOKENS = ("sol_sql", "test_case", "gold", "evaluator")


class SpoolValidationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class SpoolReceipt(BaseModel, frozen=True, extra="forbid"):
    attempt_id: UUID
    event_count: int = Field(ge=0)
    spool_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    archive_path: str = Field(min_length=1, max_length=1024)


class SpoolWriter:
    """Append-only agent-visible JSONL writer with a hard size cap."""

    def __init__(self, path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        if max_bytes <= 0:
            raise ValueError("spool max_bytes must be positive")
        self._path = path
        self._max_bytes = max_bytes
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch(exist_ok=True)  # an empty spool is a valid spool

    @property
    def path(self) -> Path:
        return self._path

    def append(self, event: dict[str, object]) -> None:
        line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        encoded = line.encode("utf-8")
        current = self._path.stat().st_size if self._path.exists() else 0
        if current + len(encoded) > self._max_bytes:
            raise SpoolValidationError("spool_size_cap_exceeded")
        with self._path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)


def _reject_forbidden_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            for token in FORBIDDEN_KEY_TOKENS:
                if token in lowered:
                    raise SpoolValidationError("forbidden_payload_key")
            _reject_forbidden_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden_keys(child)


def _parse_event(line: str) -> dict[str, object]:
    try:
        event = json.loads(line)
    except json.JSONDecodeError as error:
        raise SpoolValidationError("spool_line_not_json") from error
    if not isinstance(event, dict):
        raise SpoolValidationError("spool_line_not_object")
    required = {"run_scope_digest", "attempt_id", "phase", "sequence", "event_type", "payload"}
    if not required <= set(event):
        raise SpoolValidationError("spool_event_schema_invalid")
    if not isinstance(event["sequence"], int) or isinstance(event["sequence"], bool):
        raise SpoolValidationError("spool_event_schema_invalid")
    if not isinstance(event["payload"], dict):
        raise SpoolValidationError("spool_event_schema_invalid")
    _reject_forbidden_keys(event)
    return event


class SpoolImporter:
    """Validate one agent spool and archive it read-only for the record."""

    def __init__(self, archive_dir: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._archive_dir = archive_dir
        self._max_bytes = max_bytes

    def validate_and_import(self, path: Path, *, run_scope_digest: str, attempt_id: UUID) -> SpoolReceipt:
        if not path.is_file():
            raise SpoolValidationError("spool_file_missing")
        if path.stat().st_size > self._max_bytes:
            raise SpoolValidationError("spool_size_cap_exceeded")

        from hashlib import sha256

        raw = path.read_bytes()
        digest = sha256(raw).hexdigest()
        last_sequence: dict[str, int] = {}
        event_count = 0
        for line in raw.decode("utf-8").splitlines():
            if not line.strip():
                continue
            event = _parse_event(line)
            if event["run_scope_digest"] != run_scope_digest:
                raise SpoolValidationError("spool_scope_mismatch")
            if event["attempt_id"] != str(attempt_id):
                raise SpoolValidationError("spool_attempt_mismatch")
            phase = str(event["phase"])
            sequence = int(event["sequence"])  # type: ignore[call-overload]
            if phase in last_sequence and sequence <= last_sequence[phase]:
                raise SpoolValidationError("spool_sequence_regression")
            last_sequence[phase] = sequence
            event_count += 1

        self._archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self._archive_dir / f"{attempt_id}.jsonl"
        os.replace(path, archive_path)
        os.chmod(archive_path, stat.S_IREAD)
        return SpoolReceipt(
            attempt_id=attempt_id,
            event_count=event_count,
            spool_sha256=digest,
            archive_path=str(archive_path),
        )
