"""Attempt-scoped storage for provider-private assistant turns."""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from commerce_agent.model.contracts import ProviderTurnRef
from commerce_agent.model.errors import ModelStateError

_IDLE_TTL = timedelta(hours=24)
_ABSOLUTE_TTL = timedelta(days=7)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _payload_sha256(turn: "PrivateProviderTurn") -> str:
    payload = {
        "content": turn.content,
        "reasoning_content": turn.reasoning_content,
        "tool_calls": json.loads(turn.tool_calls_json),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class Clock(Protocol):
    """Minimal injectable UTC clock used by store adapters."""

    def now(self) -> datetime:
        """Return the current timezone-aware instant."""


class AttemptRef(BaseModel, frozen=True, extra="forbid"):
    """Public-free identity used to clear one attempt's private state."""

    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_id: UUID


class PrivateProviderTurn(BaseModel, frozen=True, extra="forbid"):
    """Complete provider assistant payload retained behind an opaque reference."""

    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_id: UUID
    sequence: int = Field(ge=0)
    provider: Literal["deepseek"]
    model: str = Field(min_length=1, max_length=256)
    content: str | None = Field(default=None, max_length=262_144, repr=False)
    reasoning_content: str | None = Field(default=None, max_length=1_048_576, repr=False)
    tool_calls_json: str = Field(max_length=262_144, repr=False)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_tool_call_ids: tuple[str, ...] = Field(max_length=8)
    token_weight: int = Field(ge=0)
    created_at: datetime
    last_used_at: datetime
    absolute_expires_at: datetime

    @field_validator("tool_calls_json")
    @classmethod
    def validate_tool_calls_json(cls, value: str) -> str:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError) as error:
            raise ValueError("tool_calls_json must be valid JSON") from error
        if not isinstance(parsed, list):
            raise TypeError("tool_calls_json must encode a list")
        if value != _canonical_json(parsed):
            raise ValueError("tool_calls_json must use canonical JSON encoding")
        return value

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "PrivateProviderTurn":
        for field_name in ("created_at", "last_used_at", "absolute_expires_at"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        if self.last_used_at < self.created_at:
            raise ValueError("last_used_at cannot precede created_at")
        if self.absolute_expires_at != self.created_at + _ABSOLUTE_TTL:
            raise ValueError("absolute_expires_at must be exactly seven days after created_at")
        if len(set(self.expected_tool_call_ids)) != len(self.expected_tool_call_ids):
            raise ValueError("expected tool call ids must be unique")
        parsed_calls = json.loads(self.tool_calls_json)
        parsed_call_ids: list[str] = []
        for call in parsed_calls:
            if not isinstance(call, dict) or not isinstance(call.get("id"), str):
                raise TypeError("tool calls must contain string ids")
            parsed_call_ids.append(call["id"])
        if tuple(parsed_call_ids) != self.expected_tool_call_ids:
            raise ValueError("expected tool call ids must match canonical tool calls")
        return self


class ProviderTurnStore(Protocol):
    """Storage seam shared by memory and PostgreSQL private-turn adapters."""

    async def save(self, turn: PrivateProviderTurn) -> ProviderTurnRef:
        """Save one private turn and return its opaque public reference."""

    async def resolve(self, ref: ProviderTurnRef) -> PrivateProviderTurn:
        """Resolve and renew one valid same-scope reference."""

    async def delete_attempt(self, attempt: AttemptRef) -> None:
        """Destroy private payloads for exactly one scope and attempt."""


class InMemoryProviderTurnStore:
    """Process-local private turn store for tests and BIRD attempts."""

    def __init__(self, *, clock: Clock) -> None:
        self._clock = clock
        self._turns: dict[UUID, PrivateProviderTurn] = {}

    async def save(self, turn: PrivateProviderTurn) -> ProviderTurnRef:
        """Validate and retain one private assistant turn."""

        if _payload_sha256(turn) != turn.payload_sha256:
            raise ModelStateError(
                "state_digest_mismatch",
                "provider turn payload digest does not match",
                retryable=False,
            )
        if any(
            stored.scope_digest == turn.scope_digest
            and stored.attempt_id == turn.attempt_id
            and stored.sequence == turn.sequence
            for stored in self._turns.values()
        ):
            raise ModelStateError(
                "state_sequence_duplicate",
                "provider turn sequence already exists for this attempt",
                retryable=False,
            )
        turn_id = uuid4()
        self._turns[turn_id] = turn
        return ProviderTurnRef(
            turn_id=turn_id,
            scope_digest=turn.scope_digest,
            attempt_id=turn.attempt_id,
            sequence=turn.sequence,
            payload_sha256=turn.payload_sha256,
            expected_tool_call_ids=turn.expected_tool_call_ids,
            token_weight=turn.token_weight,
            expires_at=turn.absolute_expires_at,
        )

    async def resolve(self, ref: ProviderTurnRef) -> PrivateProviderTurn:
        """Resolve one exact reference and renew its idle timestamp."""

        turn = self._turns.get(ref.turn_id)
        if turn is None:
            raise ModelStateError(
                "state_not_found",
                "provider turn not found",
                retryable=False,
            )
        bindings = (
            ("scope", ref.scope_digest, turn.scope_digest),
            ("attempt", ref.attempt_id, turn.attempt_id),
            ("sequence", ref.sequence, turn.sequence),
            ("payload digest", ref.payload_sha256, turn.payload_sha256),
            ("expected tool calls", ref.expected_tool_call_ids, turn.expected_tool_call_ids),
            ("token weight", ref.token_weight, turn.token_weight),
            ("expiry", ref.expires_at, turn.absolute_expires_at),
        )
        for label, supplied, stored in bindings:
            if supplied != stored:
                raise ModelStateError(
                    "state_binding_mismatch",
                    f"provider turn reference {label} does not match saved turn",
                    retryable=False,
                )
        now = self._clock.now()
        if now >= turn.absolute_expires_at or now - turn.last_used_at > _IDLE_TTL:
            raise ModelStateError(
                "state_expired",
                "provider turn state expired",
                retryable=False,
            )
        renewed = turn.model_copy(update={"last_used_at": now})
        self._turns[ref.turn_id] = renewed
        return renewed

    async def delete_attempt(self, attempt: AttemptRef) -> None:
        """Delete only turns bound to the supplied scope and attempt."""

        matching = tuple(
            turn_id
            for turn_id, turn in self._turns.items()
            if turn.scope_digest == attempt.scope_digest and turn.attempt_id == attempt.attempt_id
        )
        for turn_id in matching:
            del self._turns[turn_id]
