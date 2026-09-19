import pytest
from pydantic import ValidationError

from commerce_agent.value_resolver.contracts import (
    Confidence,
    MatchMethod,
    ResolutionStatus,
    ValueCandidate,
    ValueDomain,
    ValueResolutionRequest,
    ValueResolutionResult,
)
from commerce_agent.value_resolver.errors import ValueDomainUnavailable

MANIFEST_SHA256 = "a" * 64


def candidate(method: MatchMethod = MatchMethod.EXACT) -> ValueCandidate:
    return ValueCandidate(
        canonical_value="SP",
        display_label="São Paulo",
        match_method=method,
        confidence=Confidence.HIGH,
        support_count=10,
        evidence_ref="value:customer_state:" + "b" * 16,
    )


def test_request_is_frozen_and_rejects_unknown_domains() -> None:
    request = ValueResolutionRequest(
        domain=ValueDomain.CUSTOMER_CITY,
        raw_text=" São Paulo ",
    )

    with pytest.raises(ValidationError):
        request.raw_text = "Rio"
    with pytest.raises(ValidationError):
        ValueResolutionRequest(domain="customer_name", raw_text="Ana")


@pytest.mark.parametrize("raw_text", ["", "   "])
def test_request_rejects_blank_input(raw_text: str) -> None:
    with pytest.raises(ValidationError):
        ValueResolutionRequest(domain=ValueDomain.CUSTOMER_CITY, raw_text=raw_text)


@pytest.mark.parametrize("raw_text", ["xyz123", "a" * 33])
def test_request_rejects_invalid_id_input(raw_text: str) -> None:
    with pytest.raises(ValidationError):
        ValueResolutionRequest(domain=ValueDomain.ORDER_ID, raw_text=raw_text)


def test_resolution_enums_are_closed() -> None:
    assert {status.value for status in ResolutionStatus} == {
        "resolved",
        "ambiguous",
        "not_found",
        "too_broad",
    }
    assert {method.value for method in MatchMethod} == {
        "exact",
        "normalized_exact",
        "alias",
        "prefix",
        "trigram",
    }


@pytest.mark.parametrize(
    ("status", "candidates"),
    [
        (ResolutionStatus.RESOLVED, ()),
        (ResolutionStatus.RESOLVED, (candidate(), candidate())),
        (ResolutionStatus.AMBIGUOUS, ()),
        (ResolutionStatus.NOT_FOUND, (candidate(),)),
        (ResolutionStatus.TOO_BROAD, (candidate(),)),
    ],
)
def test_result_rejects_candidate_shapes_that_conflict_with_status(
    status: ResolutionStatus,
    candidates: tuple[ValueCandidate, ...],
) -> None:
    with pytest.raises(ValidationError):
        ValueResolutionResult(
            status=status,
            domain=ValueDomain.CUSTOMER_STATE,
            normalized_input="sp",
            candidates=candidates,
            data_manifest_sha256=MANIFEST_SHA256,
            catalog_revision="retail-catalog-v1",
        )


def test_result_is_immutable_and_caps_candidates_at_ten() -> None:
    result = ValueResolutionResult(
        status=ResolutionStatus.AMBIGUOUS,
        domain=ValueDomain.CUSTOMER_STATE,
        normalized_input="sp",
        candidates=tuple(candidate(MatchMethod.TRIGRAM) for _ in range(10)),
        data_manifest_sha256=MANIFEST_SHA256,
        catalog_revision="retail-catalog-v1",
    )

    assert len(result.candidates) == 10
    with pytest.raises(ValidationError):
        result.candidates = ()
    with pytest.raises(ValidationError):
        ValueResolutionResult(
            status=ResolutionStatus.AMBIGUOUS,
            domain=ValueDomain.CUSTOMER_STATE,
            normalized_input="sp",
            candidates=tuple(candidate(MatchMethod.TRIGRAM) for _ in range(11)),
            data_manifest_sha256=MANIFEST_SHA256,
            catalog_revision="retail-catalog-v1",
        )


def test_resolver_error_exposes_only_stable_reason_and_safe_message() -> None:
    error = ValueDomainUnavailable("domain_unavailable", "domain is unavailable")

    assert error.reason_code == "domain_unavailable"
    assert str(error) == "domain is unavailable"
