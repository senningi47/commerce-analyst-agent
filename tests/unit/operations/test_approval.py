from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from commerce_agent.operations._approval import (
    ApprovalService,
    FixedClock,
    FixedNonceSource,
    HmacApprovalKeyring,
    StoredApproval,
)
from commerce_agent.operations.contracts import ActorContext, ActorRole, ProposalRef
from commerce_agent.operations.errors import ApprovalError, OperationAuthorizationError

NOW = datetime(2026, 9, 6, 4, 0, tzinfo=UTC)


def actor(actor_id: int) -> ActorContext:
    return ActorContext(
        actor_id=UUID(int=actor_id),
        role=ActorRole.APPROVER,
        authentication_ref=f"session:verified:{actor_id}",
        authenticated_at=NOW,
    )


def approved_fixture() -> tuple[ApprovalService, StoredApproval, object]:
    service = ApprovalService(
        clock=FixedClock(NOW),
        nonce_source=FixedNonceSource(bytes(range(32))),
        keyring=HmacApprovalKeyring({1: b"day4-test-key"}, active_version=1),
    )
    grant, nonce_digest = service.issue_grant(
        proposal_ref=ProposalRef(proposal_id=UUID(int=16), version=1),
        payload_sha256="a" * 64,
        requester_id=UUID(int=1),
        approver=actor(2),
        target_versions={"investigation_task": 0},
    )
    stored = StoredApproval(
        proposal_ref=ProposalRef(proposal_id=UUID(int=16), version=1),
        payload_sha256="a" * 64,
        requester_id=UUID(int=1),
        approver_id=actor(2).actor_id,
        target_versions={"investigation_task": 0},
        expires_at=NOW + timedelta(minutes=10),
        nonce_digest=nonce_digest,
        key_version=1,
    )
    return service, stored, grant


def test_grant_is_256_bit_urlsafe_and_expires_in_exactly_ten_minutes() -> None:
    _service, stored, grant = approved_fixture()

    assert len(grant.nonce) == 43  # type: ignore[attr-defined]
    assert grant.expires_at == NOW + timedelta(minutes=10)  # type: ignore[attr-defined]
    assert len(stored.nonce_digest) == 64


@pytest.mark.parametrize(
    ("field", "value", "reason_code"),
    [
        ("payload_sha256", "b" * 64, "signature_invalid"),
        ("approver_id", UUID(int=3), "grant_actor_mismatch"),
        ("proposal_version", 2, "signature_invalid"),
        ("key_version", 99, "approval_key_unavailable"),
    ],
)
def test_grant_changes_fail_closed(field: str, value: object, reason_code: str) -> None:
    service, stored, grant = approved_fixture()
    changed = grant.model_copy(update={field: value})  # type: ignore[attr-defined]
    with pytest.raises((ApprovalError, OperationAuthorizationError)) as caught:
        service.verify_grant(changed, stored=stored, actor=actor(2))
    assert caught.value.reason_code == reason_code


def test_grant_expiry_is_inclusive() -> None:
    _service, stored, grant = approved_fixture()
    expired_service = ApprovalService(
        clock=FixedClock(stored.expires_at),
        nonce_source=FixedNonceSource(bytes(range(32))),
        keyring=HmacApprovalKeyring({1: b"day4-test-key"}, active_version=1),
    )

    with pytest.raises(ApprovalError) as caught:
        expired_service.verify_grant(grant, stored=stored, actor=actor(2))  # type: ignore[arg-type]
    assert caught.value.reason_code == "grant_expired"
