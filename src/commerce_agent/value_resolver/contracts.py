"""Public immutable contracts for deterministic business-value resolution."""

import re
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class ValueDomain(StrEnum):
    CUSTOMER_STATE = "customer_state"
    SELLER_STATE = "seller_state"
    ORDER_STATUS = "order_status"
    PAYMENT_TYPE = "payment_type"
    PRODUCT_CATEGORY = "product_category"
    CUSTOMER_CITY = "customer_city"
    SELLER_CITY = "seller_city"
    SELLER_ID = "seller_id"
    ORDER_ID = "order_id"


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    TOO_BROAD = "too_broad"


class MatchMethod(StrEnum):
    EXACT = "exact"
    NORMALIZED_EXACT = "normalized_exact"
    ALIAS = "alias"
    PREFIX = "prefix"
    TRIGRAM = "trigram"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ValueResolutionRequest(BaseModel, frozen=True):
    domain: ValueDomain
    raw_text: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_input(self) -> "ValueResolutionRequest":
        normalized = self.raw_text.strip().casefold()
        if not normalized:
            raise ValueError("raw_text must contain a non-whitespace value")
        if self.domain in {ValueDomain.SELLER_ID, ValueDomain.ORDER_ID} and not re.fullmatch(
            r"[0-9a-f]{1,32}", normalized
        ):
            raise ValueError("ID input must contain at most 32 hexadecimal characters")
        return self


class ValueCandidate(BaseModel, frozen=True):
    canonical_value: str = Field(min_length=1)
    display_label: str = Field(min_length=1)
    match_method: MatchMethod
    confidence: Confidence
    support_count: int = Field(ge=1)
    evidence_ref: str = Field(pattern=r"^value:[a-z_]+:[0-9a-f]{16}$")


class ValueResolutionResult(BaseModel, frozen=True):
    status: ResolutionStatus
    domain: ValueDomain
    normalized_input: str
    candidates: tuple[ValueCandidate, ...] = Field(max_length=10)
    data_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_revision: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_candidate_shape(self) -> "ValueResolutionResult":
        candidate_count = len(self.candidates)
        if self.status is ResolutionStatus.RESOLVED and candidate_count != 1:
            raise ValueError("resolved results require exactly one candidate")
        if self.status is ResolutionStatus.AMBIGUOUS and not 1 <= candidate_count <= 10:
            raise ValueError("ambiguous results require between one and ten candidates")
        if (
            self.status in {ResolutionStatus.NOT_FOUND, ResolutionStatus.TOO_BROAD}
            and candidate_count
        ):
            raise ValueError("not_found and too_broad results cannot contain candidates")
        return self
