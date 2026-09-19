"""Immutable DeepSeek price snapshots and Decimal cost calculation."""

from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from commerce_agent.model.contracts import CostEstimate, ReportedUsage


class PriceSnapshot(BaseModel, frozen=True, extra="forbid"):
    snapshot_id: str = Field(min_length=1, max_length=128)
    currency: Literal["USD"]
    peak_windows_utc: tuple[tuple[str, str], ...]
    peak_cache_hit_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    peak_cache_miss_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    peak_output_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    off_peak_cache_hit_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    off_peak_cache_miss_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    off_peak_output_per_million: Decimal = Field(ge=0, allow_inf_nan=False)

    @field_validator("peak_windows_utc")
    @classmethod
    def validate_windows(cls, windows: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        for start, end in windows:
            try:
                start_time = time.fromisoformat(start)
                end_time = time.fromisoformat(end)
            except ValueError as error:
                raise ValueError("peak windows must use HH:MM") from error
            if start_time >= end_time:
                raise ValueError("peak window start must precede end")
        return windows


class CostCalculator:
    def __init__(self, snapshot: PriceSnapshot) -> None:
        self._snapshot = snapshot

    def calculate(self, usage: ReportedUsage, sent_at: datetime) -> CostEstimate:
        if sent_at.tzinfo is None or sent_at.utcoffset() is None:
            raise ValueError("sent_at must be timezone-aware")
        utc = sent_at.astimezone(UTC)
        current = utc.time().replace(tzinfo=None)
        is_peak = utc.weekday() < 5 and any(
            time.fromisoformat(start) <= current < time.fromisoformat(end)
            for start, end in self._snapshot.peak_windows_utc
        )
        prefix = "peak" if is_peak else "off_peak"
        divisor = Decimal(1_000_000)
        amount = (
            Decimal(usage.cache_hit_tokens)
            * getattr(self._snapshot, f"{prefix}_cache_hit_per_million")
            + Decimal(usage.cache_miss_tokens)
            * getattr(self._snapshot, f"{prefix}_cache_miss_per_million")
            + Decimal(usage.completion_tokens)
            * getattr(self._snapshot, f"{prefix}_output_per_million")
        ) / divisor
        return CostEstimate(
            status="estimated",
            price_snapshot_id=self._snapshot.snapshot_id,
            currency="USD",
            amount=amount,
            price_band="peak" if is_peak else "off_peak",
        )
