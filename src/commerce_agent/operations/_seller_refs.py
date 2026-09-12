"""Evidence-bound opaque seller references for trusted Product targeting."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from commerce_agent.operations._approval import Clock
from commerce_agent.operations._canonical import canonical_json_bytes, utc_z
from commerce_agent.operations.contracts import (
    SellerCandidateEvidence,
    SellerRef,
    SellerTargetRequest,
)
from commerce_agent.operations.errors import OperationContractError


@dataclass(frozen=True)
class PrivateSellerObservation:
    seller_id: str
    numerator: int
    denominator: int
    normalized_value: Decimal


@dataclass(frozen=True)
class PrivateSellerTarget:
    seller_id: str
    evidence_digest: str


class SellerIdentityStore(Protocol):
    async def find_candidates(
        self, request: SellerTargetRequest
    ) -> tuple[PrivateSellerObservation, ...]: ...

    async def iter_allowed_identities(self) -> tuple[str, ...]: ...


class InMemorySellerIdentityStore:
    def __init__(self, observations: tuple[PrivateSellerObservation, ...]) -> None:
        self._observations = observations

    async def find_candidates(
        self, request: SellerTargetRequest
    ) -> tuple[PrivateSellerObservation, ...]:
        return tuple(
            item
            for item in self._observations
            if item.denominator >= request.minimum_denominator
            and item.normalized_value >= request.anomaly_threshold
        )

    async def iter_allowed_identities(self) -> tuple[str, ...]:
        return tuple(item.seller_id for item in self._observations)


class SellerRefKeyring:
    def __init__(self, keys: Mapping[int, bytes], *, active_version: int) -> None:
        if active_version not in keys or any(not key for key in keys.values()):
            raise ValueError("active seller reference key is required")
        self._keys = dict(keys)
        self.active_version = active_version

    def digest_identity(self, seller_id: str, *, key_version: int | None = None) -> str:
        version = self.active_version if key_version is None else key_version
        key = self._keys.get(version)
        if key is None:
            raise OperationContractError("seller_ref_key_unavailable", retryable=False)
        return hmac.new(key, b"identity:" + seller_id.encode(), hashlib.sha256).hexdigest()

    def sign(self, payload: bytes, *, key_version: int) -> str:
        key = self._keys.get(key_version)
        if key is None:
            raise OperationContractError("seller_ref_key_unavailable", retryable=False)
        return hmac.new(key, b"reference:" + payload, hashlib.sha256).hexdigest()

    def verify(self, payload: bytes, signature: str, *, key_version: int) -> bool:
        return hmac.compare_digest(self.sign(payload, key_version=key_version), signature)


class SellerTargetResolver:
    def __init__(
        self, *, store: SellerIdentityStore, keyring: SellerRefKeyring, clock: Clock
    ) -> None:
        self._store = store
        self._keyring = keyring
        self._clock = clock

    async def find_candidates(
        self, request: SellerTargetRequest
    ) -> tuple[SellerCandidateEvidence, ...]:
        observations = await self._store.find_candidates(request)
        expires_at = self._clock.now() + timedelta(minutes=15)
        return tuple(
            SellerCandidateEvidence(
                seller_ref=self._issue(
                    seller_id=item.seller_id,
                    evidence_digest=request.evidence_digest,
                    expires_at=expires_at,
                ),
                numerator=item.numerator,
                denominator=item.denominator,
                normalized_value=item.normalized_value.quantize(Decimal("0.000001")),
                observation_started_at=request.observation_started_at,
                observation_ended_at=request.observation_ended_at,
                evidence_digest=request.evidence_digest,
            )
            for item in observations
        )

    def _issue(self, *, seller_id: str, evidence_digest: str, expires_at) -> SellerRef:
        key_version = self._keyring.active_version
        payload = {
            "evidence_digest": evidence_digest,
            "expires_at": utc_z(expires_at),
            "key_version": key_version,
            "namespace": "product:seller-target",
            "schema_version": 1,
            "seller_digest": self._keyring.digest_identity(
                seller_id, key_version=key_version
            ),
        }
        canonical = canonical_json_bytes(payload)
        signature = self._keyring.sign(canonical, key_version=key_version).encode("ascii")
        token = base64.urlsafe_b64encode(canonical + b"." + signature).decode().rstrip("=")
        return SellerRef(namespace="product:seller-target", token=token)

    async def resolve(
        self, reference: SellerRef, *, expected_evidence_digest: str
    ) -> PrivateSellerTarget:
        if reference.namespace != "product:seller-target":
            raise OperationContractError("seller_ref_namespace_mismatch", retryable=False)
        payload = self._decode(reference.token)
        if payload["namespace"] != reference.namespace:
            raise OperationContractError("seller_ref_namespace_mismatch", retryable=False)
        if payload["evidence_digest"] != expected_evidence_digest:
            raise OperationContractError("seller_ref_evidence_mismatch", retryable=False)
        try:
            expires_at = datetime_from_utc_z(payload["expires_at"])
        except (TypeError, ValueError) as exc:
            raise OperationContractError("seller_ref_invalid", retryable=False) from exc
        if self._clock.now() >= expires_at:
            raise OperationContractError("seller_ref_expired", retryable=False)

        identities = await self._store.iter_allowed_identities()
        matches = tuple(
            identity
            for identity in identities
            if hmac.compare_digest(
                self._keyring.digest_identity(
                    identity, key_version=int(payload["key_version"])
                ),
                str(payload["seller_digest"]),
            )
        )
        if not matches:
            raise OperationContractError("seller_ref_not_found", retryable=False)
        if len(matches) != 1:
            raise OperationContractError("seller_ref_not_unique", retryable=False)
        return PrivateSellerTarget(
            seller_id=matches[0], evidence_digest=expected_evidence_digest
        )

    def _decode(self, token: str) -> dict[str, object]:
        try:
            padded = token + "=" * (-len(token) % 4)
            encoded = base64.urlsafe_b64decode(padded.encode("ascii"))
            canonical, signature_bytes = encoded.rsplit(b".", 1)
            payload = json.loads(canonical)
            if set(payload) != {
                "evidence_digest",
                "expires_at",
                "key_version",
                "namespace",
                "schema_version",
                "seller_digest",
            }:
                raise ValueError
            if payload["schema_version"] != 1:
                raise ValueError
            key_version = int(payload["key_version"])
            signature = signature_bytes.decode("ascii")
            if not self._keyring.verify(canonical, signature, key_version=key_version):
                raise ValueError
            if canonical_json_bytes(payload) != canonical:
                raise ValueError
            return payload
        except OperationContractError:
            raise
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeError) as exc:
            raise OperationContractError("seller_ref_invalid", retryable=False) from exc


def datetime_from_utc_z(value: object):
    from datetime import UTC, datetime

    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(UTC)
