"""Bounded serial retry policy for model transport attempts."""

from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field


class RetryDecision(BaseModel, frozen=True, extra="forbid"):
    should_retry: bool
    delay_seconds: Decimal = Field(ge=0, allow_inf_nan=False)


class RetryPolicy(BaseModel, frozen=True, extra="forbid"):
    revision: str = "deepseek-retry-v1"
    base_delay_seconds: Decimal = Field(default=Decimal("0.5"), gt=0)
    maximum_delay_seconds: Decimal = Field(default=Decimal(30), gt=0)
    maximum_attempts: int = Field(default=3, ge=1, le=3)

    def decision(
        self,
        *,
        attempt_number: int,
        retry_after: str | None,
        jitter: Decimal,
    ) -> RetryDecision:
        if attempt_number >= self.maximum_attempts:
            return RetryDecision(should_retry=False, delay_seconds=Decimal(0))
        delay: Decimal | None = None
        if retry_after is not None:
            try:
                parsed = Decimal(retry_after)
            except InvalidOperation:
                parsed = Decimal(-1)
            if parsed >= 0 and parsed.is_finite():
                delay = parsed
        if delay is None:
            delay = self.base_delay_seconds * (Decimal(2) ** (attempt_number - 1))
            delay += max(Decimal(0), jitter)
        return RetryDecision(
            should_retry=True,
            delay_seconds=min(delay, self.maximum_delay_seconds),
        )
