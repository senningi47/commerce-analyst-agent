from datetime import UTC, datetime
from decimal import Decimal

import pytest

from commerce_agent.operations._approval import ApprovalEnvelope, HmacApprovalKeyring
from commerce_agent.operations._canonical import canonical_json_bytes, normalized_decimal
from commerce_agent.operations.errors import OperationContractError


def valid_envelope_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "proposal_id": "00000000-0000-0000-0000-000000000010",
        "proposal_version": 1,
        "payload_sha256": "a" * 64,
        "requester_id": "00000000-0000-0000-0000-000000000001",
        "approver_id": "00000000-0000-0000-0000-000000000002",
        "target_versions": {"investigation_task": 0},
        "expires_at": datetime(2026, 9, 6, 4, 10, tzinfo=UTC),
        "nonce": "AQID",
    }


def test_approval_envelope_matches_reviewed_golden_vector() -> None:
    envelope = ApprovalEnvelope.model_validate(valid_envelope_payload())
    canonical = canonical_json_bytes(envelope)
    signature = HmacApprovalKeyring({1: b"day4-test-key"}, active_version=1).sign(
        key_version=1,
        message=canonical,
    )

    assert canonical.decode() == (
        '{"approver_id":"00000000-0000-0000-0000-000000000002",'
        '"expires_at":"2026-09-06T04:10:00Z","nonce":"AQID",'
        '"payload_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"proposal_id":"00000000-0000-0000-0000-000000000010",'
        '"proposal_version":1,"requester_id":"00000000-0000-0000-0000-000000000001",'
        '"schema_version":1,"target_versions":{"investigation_task":0}}'
    )
    assert signature == "0334990d9927ca23561f2ad5c42f868ccd56e2239db7d6b1756b4ab2b8f409c4"


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_non_finite_decimal_is_rejected(value: Decimal) -> None:
    with pytest.raises(OperationContractError) as caught:
        normalized_decimal(value, scale=4)
    assert caught.value.reason_code == "decimal_not_finite"


def test_normalization_preserves_unicode_and_uses_fixed_decimal_scale() -> None:
    assert normalized_decimal(Decimal("0.2"), scale=4) == "0.2000"
    assert canonical_json_bytes({"b": "风险", "a": 1}) == b'{"a":1,"b":"\xe9\xa3\x8e\xe9\x99\xa9"}'


def test_naive_datetime_is_rejected_instead_of_assuming_timezone() -> None:
    with pytest.raises(ValueError, match="timezone"):
        ApprovalEnvelope.model_validate(
            valid_envelope_payload()
            | {"expires_at": datetime(2026, 9, 6, 4, 10)}  # noqa: DTZ001
        )
