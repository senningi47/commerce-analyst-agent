"""Offline tests for the Day 6 capability probe verification logic.

Every branch of `verify_probe_response` is exercised with synthetic gateway
responses — running the probe itself is a paid action and stays behind its own
authorization gate (<= USD 0.01).
"""

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from commerce_agent.model.contracts import (
    CostEstimate,
    FinalOutput,
    FinishReason,
    ModelAttemptSummary,
    ModelResponse,
    ProviderTurnRef,
    ReportedUsage,
    ToolCall,
    ToolCallOutput,
)
from commerce_agent.model.snapshots import load_reviewed_model_snapshots
from scripts.probe_model_capability import build_probe_request, verify_probe_response

SUNDAY_SENT_AT = datetime(2026, 9, 13, 8, 30, tzinfo=UTC)  # inside a UTC peak window, but Sunday
from commerce_agent.model._pricing import PriceSnapshot

PRICES = PriceSnapshot(
    snapshot_id="deepseek-flash-usd-2026-09-12",
    currency="USD",
    peak_windows_utc=(("01:00", "04:00"), ("06:00", "10:00")),
    peak_cache_hit_per_million=Decimal("0.006"),
    peak_cache_miss_per_million=Decimal("0.3"),
    peak_output_per_million=Decimal("1.2"),
    off_peak_cache_hit_per_million=Decimal("0.003"),
    off_peak_cache_miss_per_million=Decimal("0.15"),
    off_peak_output_per_million=Decimal("0.6"),
)


def _usage() -> ReportedUsage:
    return ReportedUsage(
        status="reported",
        prompt_tokens=550,
        cache_hit_tokens=10,
        cache_miss_tokens=540,
        completion_tokens=120,
        reasoning_tokens=40,
        total_tokens=670,
    )


def _cost(amount: Decimal = Decimal("0.00015303"), band: str = "off_peak") -> CostEstimate:
    return CostEstimate(
        status="estimated",
        price_snapshot_id="deepseek-flash-usd-2026-09-12",
        currency="USD",
        amount=amount,
        price_band=band,
    )


def _response(
    *,
    output: FinalOutput | ToolCallOutput | None = None,
    finish_reason: FinishReason = FinishReason.TOOL_CALLS,
    actual_model: str = "deepseek-flash",
    cost: CostEstimate | None = None,
    extra_attempt: ModelAttemptSummary | None = None,
) -> ModelResponse:
    usage = _usage()
    first = ModelAttemptSummary(
        attempt_number=1,
        sent_at=SUNDAY_SENT_AT,
        completed_at=SUNDAY_SENT_AT + timedelta(seconds=1),
        outcome="success",
        retryable=False,
        charge_ambiguous=False,
        usage=usage,
        cost=cost or _cost(),
    ) if extra_attempt is None else extra_attempt
    second = None
    if extra_attempt is not None:
        second = ModelAttemptSummary(
            attempt_number=2,
            sent_at=SUNDAY_SENT_AT + timedelta(seconds=2),
            completed_at=SUNDAY_SENT_AT + timedelta(seconds=3),
            outcome="success",
            retryable=False,
            charge_ambiguous=False,
            usage=usage,
            cost=cost or _cost(),
        )
    attempts = (first,) if second is None else (first, second)
    if output is None:
        output = ToolCallOutput(
            type="tool_calls",
            tool_calls=(ToolCall(call_id="call_1", name="get_schema", arguments_json="{}"),),
        )
    expected_tool_call_ids = (
        ("call_1",) if output.type == "tool_calls" else ()
    )
    return ModelResponse(
        output=output,
        provider_turn_ref=ProviderTurnRef(
            turn_id=uuid.uuid4(),
            scope_digest="a" * 64,
            attempt_id=uuid.uuid4(),
            sequence=0,
            payload_sha256="b" * 64,
            expected_tool_call_ids=expected_tool_call_ids,
            token_weight=1,
            expires_at=SUNDAY_SENT_AT + timedelta(hours=1),
        ),
        finish_reason=finish_reason,
        actual_model=actual_model,
        usage=usage,
        cost=cost or _cost(),
        attempts=attempts,
    )


def _check(checks, name: str):
    return next(check for check in checks if check["name"] == name)


class TestVerifyProbeResponse:
    def test_all_checks_pass_on_healthy_response(self) -> None:
        checks = verify_probe_response(
            response=_response(),
            capability_requested_model="deepseek-flash",
            prices=PRICES,
            sent_at=SUNDAY_SENT_AT,
        )
        assert len(checks) == 5
        assert all(check["passed"] for check in checks), checks
        band_detail = _check(checks, "cost_recalculation")["detail"]
        assert band_detail["band"] == "off_peak"  # Sunday: peak window does not apply
        assert band_detail["sent_weekday"] == "Sunday"

    def test_model_echo_mismatch_fails(self) -> None:
        checks = verify_probe_response(
            response=_response(actual_model="deepseek-chat"),
            capability_requested_model="deepseek-flash",
            prices=PRICES,
            sent_at=SUNDAY_SENT_AT,
        )
        assert _check(checks, "model_echo")["passed"] is False

    def test_final_output_without_tool_call_fails_tool_check(self) -> None:
        checks = verify_probe_response(
            response=_response(
                output=FinalOutput(type="final", content="done"),
                finish_reason=FinishReason.STOP,
            ),
            capability_requested_model="deepseek-flash",
            prices=PRICES,
            sent_at=SUNDAY_SENT_AT,
        )
        assert _check(checks, "tool_call_emitted")["passed"] is False
        assert _check(checks, "tool_call_emitted")["detail"]["tool_call_count"] == 0

    def test_cost_mismatch_fails_recalculation(self) -> None:
        checks = verify_probe_response(
            response=_response(cost=_cost(amount=Decimal("0.999"))),
            capability_requested_model="deepseek-flash",
            prices=PRICES,
            sent_at=SUNDAY_SENT_AT,
        )
        assert _check(checks, "cost_recalculation")["passed"] is False

    def test_peak_weekday_cost_uses_peak_rates(self) -> None:
        monday_sent_at = datetime(2026, 9, 14, 7, 0, tzinfo=UTC)  # Monday inside 06:00-10:00
        response = _response()
        checks = verify_probe_response(
            response=response,
            capability_requested_model="deepseek-flash",
            prices=PRICES,
            sent_at=monday_sent_at,
        )
        detail = _check(checks, "cost_recalculation")["detail"]
        assert detail["sent_weekday"] == "Monday"
        assert detail["band"] == "peak"
        assert _check(checks, "cost_recalculation")["passed"] is False

    def test_failed_first_attempt_fails_accounting(self) -> None:
        usage = _usage()
        http_error_attempt = ModelAttemptSummary(
            attempt_number=1,
            sent_at=SUNDAY_SENT_AT,
            completed_at=SUNDAY_SENT_AT + timedelta(seconds=1),
            outcome="http_error",
            http_status=503,
            retryable=True,
            charge_ambiguous=False,
            usage=usage,
            cost=_cost(),
        )
        checks = verify_probe_response(
            response=_response(extra_attempt=http_error_attempt),
            capability_requested_model="deepseek-flash",
            prices=PRICES,
            sent_at=SUNDAY_SENT_AT,
        )
        assert _check(checks, "attempt_accounting")["passed"] is False
        assert _check(checks, "attempt_accounting")["detail"]["attempt_count"] == 2

    def test_build_probe_request_is_fully_bound(self, tmp_path) -> None:
        """The paid shell builds a request that satisfies every gateway guard."""
        configs = tmp_path / "configs" / "model"
        configs.mkdir(parents=True)
        for name in (
            "deepseek-flash-capability.v1.json",
            "deepseek-flash-price.2026-09-12.json",
            "deepseek-tokenizer-artifact.v1.json",
        ):
            source = Path("configs/model") / name
            if source.exists():
                shutil.copy(source, configs / name)
        if not (configs / "deepseek-flash-capability.v1.json").exists():
            import pytest

            pytest.skip("reviewed snapshot files not present")

        snapshots = load_reviewed_model_snapshots(configs)
        request = build_probe_request(config_root=configs)
        assert request.run_scope.track == "bird"
        assert request.inference.type == "disabled"
        assert request.tools[0].name == "get_schema"
        assert json.loads(request.tools[0].parameters_json)["type"] == "object"
        assert snapshots.capability.requested_model == "deepseek-flash"
