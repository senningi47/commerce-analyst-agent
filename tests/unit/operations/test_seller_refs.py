from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from commerce_agent.operations._approval import FixedClock
from commerce_agent.operations._seller_refs import (
    InMemorySellerIdentityStore,
    PrivateSellerObservation,
    SellerRefKeyring,
    SellerTargetResolver,
)
from commerce_agent.operations.contracts import MetricRef, SellerTargetRequest
from commerce_agent.operations.errors import OperationContractError

NOW = datetime(2026, 9, 6, 4, 0, tzinfo=UTC)


def private_seller_observation(
    seller_id: str, *, late_rate: str = "0.30"
) -> PrivateSellerObservation:
    return PrivateSellerObservation(
        seller_id=seller_id,
        numerator=3,
        denominator=10,
        normalized_value=Decimal(late_rate),
    )


def seller_target_request(*, evidence_digest: str, anomaly_threshold: str) -> SellerTargetRequest:
    return SellerTargetRequest(
        metric_ref=MetricRef(name="late_delivery_rate", revision="v1"),
        observation_started_at=NOW - timedelta(days=30),
        observation_ended_at=NOW,
        anomaly_threshold=Decimal(anomaly_threshold),
        minimum_denominator=5,
        status_filters=("delivered",),
        evidence_digest=evidence_digest,
    )


def seller_resolver_fixture(
    observations: tuple[PrivateSellerObservation, ...], *, now: datetime = NOW
) -> SellerTargetResolver:
    return SellerTargetResolver(
        store=InMemorySellerIdentityStore(observations),
        keyring=SellerRefKeyring({1: b"seller-test-key"}, active_version=1),
        clock=FixedClock(now),
    )


@pytest.mark.asyncio
async def test_seller_ref_is_opaque_and_resolves_one_evidence_bound_target() -> None:
    resolver = seller_resolver_fixture(
        (private_seller_observation("seller-private-001"),)
    )
    evidence_digest = "a" * 64

    candidates = await resolver.find_candidates(
        seller_target_request(evidence_digest=evidence_digest, anomaly_threshold="0.20")
    )
    reference = candidates[0].seller_ref
    resolved = await resolver.resolve(reference, expected_evidence_digest=evidence_digest)

    assert resolved.seller_id == "seller-private-001"
    assert "seller-private-001" not in reference.model_dump_json()
    assert reference.namespace == "product:seller-target"


@pytest.mark.asyncio
async def test_seller_candidate_normalizes_ratio_to_public_contract_scale() -> None:
    resolver = seller_resolver_fixture(
        (private_seller_observation("seller-private-001", late_rate="0.333333333333"),)
    )

    candidates = await resolver.find_candidates(
        seller_target_request(evidence_digest="a" * 64, anomaly_threshold="0.20")
    )

    assert candidates[0].normalized_value == Decimal("0.333333")


@pytest.mark.asyncio
async def test_seller_ref_rejects_tamper_evidence_mismatch_and_expiry() -> None:
    resolver = seller_resolver_fixture((private_seller_observation("seller-private-001"),))
    reference = (
        await resolver.find_candidates(
            seller_target_request(evidence_digest="a" * 64, anomaly_threshold="0.20")
        )
    )[0].seller_ref

    changed = reference.model_copy(update={"token": reference.token[:-1] + "A"})
    with pytest.raises(OperationContractError) as tampered:
        await resolver.resolve(changed, expected_evidence_digest="a" * 64)
    assert tampered.value.reason_code == "seller_ref_invalid"

    with pytest.raises(OperationContractError) as mismatch:
        await resolver.resolve(reference, expected_evidence_digest="b" * 64)
    assert mismatch.value.reason_code == "seller_ref_evidence_mismatch"

    expired = seller_resolver_fixture(
        (private_seller_observation("seller-private-001"),),
        now=NOW + timedelta(minutes=16),
    )
    with pytest.raises(OperationContractError) as expiry:
        await expired.resolve(reference, expected_evidence_digest="a" * 64)
    assert expiry.value.reason_code == "seller_ref_expired"
