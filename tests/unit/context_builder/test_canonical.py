from datetime import UTC, datetime

import pytest

from commerce_agent.context_builder._canonical import canonical_json, sha256_canonical


def test_canonical_json_is_order_independent_and_utf8_stable() -> None:
    left = canonical_json({"b": 2, "a": "São Paulo"})
    right = canonical_json({"a": "São Paulo", "b": 2})
    assert left == right == '{"a":"São Paulo","b":2}'
    assert sha256_canonical({"b": 2, "a": "São Paulo"}) == sha256_canonical(
        {"a": "São Paulo", "b": 2}
    )


def test_canonical_json_normalizes_utc_datetimes_and_rejects_nonfinite() -> None:
    assert canonical_json({"at": datetime(2026, 9, 5, tzinfo=UTC)}) == (
        '{"at":"2026-09-05T00:00:00Z"}'
    )
    with pytest.raises(ValueError):
        canonical_json({"invalid": float("nan")})
