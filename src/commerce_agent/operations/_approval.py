"""HMAC approval envelopes and injected time/nonce ports."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from pydantic import Field, field_validator

from commerce_agent.operations._canonical import canonical_json_bytes, utc_z
from commerce_agent.operations.contracts import (
    ActorContext,
    ExecutionGrant,
    FrozenContract,
    ProposalRef,
)
from commerce_agent.operations.errors import ApprovalError, OperationAuthorizationError


class Clock(Protocol):
    def now(self) -> datetime: ...


class NonceSource(Protocol):
    def issue(self) -> bytes: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    def __init__(self, value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone-aware clock value required")
        self._value = value

    def now(self) -> datetime:
        return self._value


class FixedNonceSource:
    def __init__(self, value: bytes) -> None:
        if len(value) != 32:
            raise ValueError("nonce source must provide exactly 256 bits")
        self._value = value

    def issue(self) -> bytes:
        return self._value


class SecretsNonceSource:
    def issue(self) -> bytes:
        return secrets.token_bytes(32)


class ApprovalEnvelope(FrozenContract):
    schema_version: int = Field(ge=1)
    proposal_id: UUID
    proposal_version: int = Field(ge=1)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requester_id: UUID
    approver_id: UUID
    target_versions: dict[str, int] = Field(min_length=1, max_length=8)
    expires_at: datetime
    nonce: str = Field(min_length=4, max_length=128)

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone-aware expiry required")
        return value

    def canonical_projection(self) -> Mapping[str, object]:
        return {
            "schema_version": self.schema_version,
            "proposal_id": str(self.proposal_id).lower(),
            "proposal_version": self.proposal_version,
            "payload_sha256": self.payload_sha256,
            "requester_id": str(self.requester_id).lower(),
            "approver_id": str(self.approver_id).lower(),
            "target_versions": dict(self.target_versions),
            "expires_at": utc_z(self.expires_at),
            "nonce": self.nonce,
        }


class StoredApproval(FrozenContract):
    proposal_ref: ProposalRef
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requester_id: UUID
    approver_id: UUID
    target_versions: dict[str, int] = Field(min_length=1, max_length=8)
    expires_at: datetime
    nonce_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    key_version: int = Field(ge=1)


class HmacApprovalKeyring:
    def __init__(self, keys: Mapping[int, bytes], *, active_version: int) -> None:
        if active_version not in keys or any(not key for key in keys.values()):
            raise ValueError("active approval key is required")
        self._keys = dict(keys)
        self.active_version = active_version

    def sign(self, *, key_version: int, message: bytes) -> str:
        key = self._keys.get(key_version)
        if key is None:
            raise ApprovalError("approval_key_unavailable", retryable=False)
        return hmac.new(key, message, hashlib.sha256).hexdigest()

    def verify(self, *, key_version: int, message: bytes, signature: str) -> bool:
        expected = self.sign(key_version=key_version, message=message)
        return hmac.compare_digest(expected, signature)


class ApprovalService:
    def __init__(self, *, clock: Clock, nonce_source: NonceSource, keyring: HmacApprovalKeyring):
        self._clock = clock
        self._nonce_source = nonce_source
        self._keyring = keyring

    def issue_grant(
        self,
        *,
        proposal_ref: ProposalRef,
        payload_sha256: str,
        requester_id: UUID,
        approver: ActorContext,
        target_versions: Mapping[str, int],
    ) -> tuple[ExecutionGrant, str]:
        raw_nonce = self._nonce_source.issue()
        if len(raw_nonce) != 32:
            raise ApprovalError("nonce_entropy_invalid", retryable=False)
        nonce = base64.urlsafe_b64encode(raw_nonce).decode("ascii").rstrip("=")
        expires_at = self._clock.now() + timedelta(minutes=10)
        envelope = ApprovalEnvelope(
            schema_version=1,
            proposal_id=proposal_ref.proposal_id,
            proposal_version=proposal_ref.version,
            payload_sha256=payload_sha256,
            requester_id=requester_id,
            approver_id=approver.actor_id,
            target_versions=dict(target_versions),
            expires_at=expires_at,
            nonce=nonce,
        )
        key_version = self._keyring.active_version
        signature = self._keyring.sign(
            key_version=key_version,
            message=canonical_json_bytes(envelope),
        )
        grant = ExecutionGrant(
            proposal_id=proposal_ref.proposal_id,
            proposal_version=proposal_ref.version,
            payload_sha256=payload_sha256,
            requester_id=requester_id,
            approver_id=approver.actor_id,
            target_versions=dict(target_versions),
            expires_at=expires_at,
            nonce=nonce,
            key_version=key_version,
            signature=signature,
        )
        return grant, hashlib.sha256(raw_nonce).hexdigest()

    def verify_grant(
        self,
        grant: ExecutionGrant,
        *,
        stored: StoredApproval,
        actor: ActorContext,
    ) -> None:
        if actor.actor_id != grant.approver_id or actor.actor_id != stored.approver_id:
            raise OperationAuthorizationError("grant_actor_mismatch", retryable=False)
        envelope = ApprovalEnvelope(
            schema_version=1,
            proposal_id=grant.proposal_id,
            proposal_version=grant.proposal_version,
            payload_sha256=grant.payload_sha256,
            requester_id=grant.requester_id,
            approver_id=grant.approver_id,
            target_versions=grant.target_versions,
            expires_at=grant.expires_at,
            nonce=grant.nonce,
        )
        if not self._keyring.verify(
            key_version=grant.key_version,
            message=canonical_json_bytes(envelope),
            signature=grant.signature,
        ):
            raise ApprovalError("signature_invalid", retryable=False)
        expected_bindings = (
            stored.proposal_ref.proposal_id,
            stored.proposal_ref.version,
            stored.payload_sha256,
            stored.requester_id,
            stored.target_versions,
            stored.expires_at,
            stored.key_version,
        )
        actual_bindings = (
            grant.proposal_id,
            grant.proposal_version,
            grant.payload_sha256,
            grant.requester_id,
            grant.target_versions,
            grant.expires_at,
            grant.key_version,
        )
        if actual_bindings != expected_bindings:
            raise ApprovalError("signature_invalid", retryable=False)
        try:
            padded = grant.nonce + "=" * (-len(grant.nonce) % 4)
            raw_nonce = base64.urlsafe_b64decode(padded.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ApprovalError("signature_invalid", retryable=False) from exc
        if not hmac.compare_digest(hashlib.sha256(raw_nonce).hexdigest(), stored.nonce_digest):
            raise ApprovalError("signature_invalid", retryable=False)
        if self._clock.now() >= grant.expires_at:
            raise ApprovalError("grant_expired", retryable=False)


def execution_grant_sha256(grant: ExecutionGrant) -> str:
    """Return a stable comparison digest without persisting the bearer grant."""
    return hashlib.sha256(
        canonical_json_bytes(grant.model_dump(mode="json"))
    ).hexdigest()
