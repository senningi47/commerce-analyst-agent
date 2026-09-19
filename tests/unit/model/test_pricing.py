from datetime import UTC, datetime
from decimal import Decimal

from commerce_agent.model._pricing import CostCalculator, PriceSnapshot
from commerce_agent.model.contracts import ReportedUsage

PEAK_SEND_TIME = datetime(2026, 9, 4, 2, tzinfo=UTC)
OFF_PEAK_SEND_TIME = datetime(2026, 9, 5, 2, tzinfo=UTC)


def calculator() -> CostCalculator:
    return CostCalculator(
        PriceSnapshot(
            snapshot_id="deepseek-v4-flash-usd-2026-09-01",
            currency="USD",
            peak_windows_utc=(("01:00", "04:00"), ("06:00", "10:00")),
            peak_cache_hit_per_million=Decimal("0.014"),
            peak_cache_miss_per_million=Decimal("0.44"),
            peak_output_per_million=Decimal("1.32"),
            off_peak_cache_hit_per_million=Decimal("0.007"),
            off_peak_cache_miss_per_million=Decimal("0.22"),
            off_peak_output_per_million=Decimal("0.66"),
        )
    )


def test_peak_and_off_peak_cost_use_decimal_without_double_charging_reasoning() -> None:
    usage = ReportedUsage(
        status="reported",
        prompt_tokens=1_000_000,
        cache_hit_tokens=250_000,
        cache_miss_tokens=750_000,
        completion_tokens=100_000,
        reasoning_tokens=80_000,
        total_tokens=1_100_000,
    )
    assert calculator().calculate(usage, PEAK_SEND_TIME).amount == Decimal("0.4655")
    assert calculator().calculate(usage, OFF_PEAK_SEND_TIME).amount == Decimal("0.23275")
