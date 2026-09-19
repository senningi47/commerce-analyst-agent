from decimal import Decimal

from commerce_agent.model._retry import RetryPolicy


def test_retry_policy_uses_bounded_retry_after_and_fallback_delays() -> None:
    policy = RetryPolicy(
        revision="deepseek-retry-v1",
        base_delay_seconds=Decimal("0.5"),
        maximum_delay_seconds=Decimal(30),
        maximum_attempts=3,
    )
    assert policy.decision(
        attempt_number=1, retry_after="40", jitter=Decimal(0)
    ).delay_seconds == Decimal(30)
    assert policy.decision(
        attempt_number=1, retry_after=None, jitter=Decimal(0)
    ).delay_seconds == Decimal("0.5")
    assert policy.decision(
        attempt_number=2, retry_after=None, jitter=Decimal(0)
    ).delay_seconds == Decimal(1)
    assert (
        policy.decision(attempt_number=3, retry_after=None, jitter=Decimal(0)).should_retry is False
    )


def test_retry_policy_ignores_invalid_retry_after_and_bounds_jitter() -> None:
    policy = RetryPolicy()
    assert policy.decision(
        attempt_number=1, retry_after="invalid", jitter=Decimal("0.25")
    ).delay_seconds == Decimal("0.75")
    assert policy.decision(
        attempt_number=2, retry_after="-3", jitter=Decimal(0)
    ).delay_seconds == Decimal(1)
