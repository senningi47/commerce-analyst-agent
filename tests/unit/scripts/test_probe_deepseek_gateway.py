import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

import scripts.probe_deepseek_gateway as probe_module
from commerce_agent.config import AppSettings
from commerce_agent.context_builder.builder import scope_digest
from commerce_agent.context_builder.profiles import ProfileRegistry
from commerce_agent.model._pricing import PriceSnapshot
from commerce_agent.model.contracts import (
    ChatMessage,
    CostEstimate,
    CostUnavailable,
    FinalOutput,
    ModelAttemptSummary,
    ModelRequest,
    ModelResponse,
    ProviderTurnRef,
    ReportedUsage,
    ThinkingConfig,
    ToolCall,
    ToolCallOutput,
    UsageUnavailable,
)
from commerce_agent.model.fake import FakeModel
from scripts.probe_deepseek_gateway import (
    PROBE_ATTEMPT_ID,
    PROBE_RUN_ID,
    PROBE_SQL,
    PROBE_SUBJECT_ID,
    ProbeBudget,
    ProbeBudgetExceeded,
    ProbeLedger,
    ProbePreflightError,
    fixed_probe_request,
    load_reviewed_snapshots,
    probe_budget,
    probe_profile,
    run_probe,
    validate_probe_preflight,
    write_probe_evidence,
)

CONFIG_ROOT = Path(__file__).parents[3] / "configs" / "model"
NOW = datetime(2026, 9, 6, 2, tzinfo=UTC)


def peak_prices() -> PriceSnapshot:
    return PriceSnapshot(
        snapshot_id="probe-prices-v1",
        currency="USD",
        peak_windows_utc=(("01:00", "04:00"),),
        peak_cache_hit_per_million=Decimal("0.014"),
        peak_cache_miss_per_million=Decimal("0.44"),
        peak_output_per_million=Decimal("1.32"),
        off_peak_cache_hit_per_million=Decimal("0.007"),
        off_peak_cache_miss_per_million=Decimal("0.22"),
        off_peak_output_per_million=Decimal("0.66"),
    )


def probe_model_request() -> ModelRequest:
    request = fixed_probe_request(CONFIG_ROOT)
    return ModelRequest(
        run_scope=request.run_scope,
        attempt_id=request.attempt_id,
        sequence=0,
        inference=ThinkingConfig(type="enabled", effort="high", max_output_tokens=256),
        messages=(ChatMessage(role="user", content="fixed probe"),),
        timeout_seconds=Decimal(30),
        capability_revision="deepseek-v4-flash-capability-v3",
        provider_user_id="a" * 32,
        prompt_policy_hash="1" * 64,
        rendered_prompt_hash="2" * 64,
        tool_hash="3" * 64,
        context_hash="4" * 64,
        config_hash=request.run_scope.config_hash,
    )


def fake_settings() -> AppSettings:
    return AppSettings(
        environment="test",
        deepseek_base_url="https://api.deepseek.com",
    )


def paid_environment() -> dict[str, str]:
    return {
        "COMMERCE_AGENT_RUN_DEEPSEEK_TESTS": "1",
        "COMMERCE_AGENT_ACCEPT_MAX_USD": "0.01",
        "LANGGRAPH_STRICT_MSGPACK": "true",
        "DEEPSEEK_API_KEY": "unit-test-key",
    }


def fake_probe_response(
    *,
    sequence: int,
    output: FinalOutput | ToolCallOutput,
    finish_reason: str,
) -> ModelResponse:
    request = fixed_probe_request(CONFIG_ROOT)
    usage = ReportedUsage(
        status="reported",
        prompt_tokens=100,
        cache_hit_tokens=0,
        cache_miss_tokens=100,
        completion_tokens=10,
        reasoning_tokens=5,
        total_tokens=110,
    )
    cost = CostEstimate(
        status="estimated",
        price_snapshot_id="deepseek-v4-flash-usd-2026-09-06",
        currency="USD",
        amount=Decimal("0.0000572"),
        price_band="peak",
    )
    expected_ids = (
        tuple(call.call_id for call in output.tool_calls)
        if isinstance(output, ToolCallOutput)
        else ()
    )
    attempt = ModelAttemptSummary(
        attempt_number=1,
        sent_at=NOW,
        completed_at=NOW,
        outcome="success",
        http_status=200,
        retryable=False,
        charge_ambiguous=False,
        usage=usage,
        cost=cost,
    )
    return ModelResponse(
        output=output,
        provider_turn_ref=ProviderTurnRef(
            turn_id=UUID(int=sequence + 1),
            scope_digest=scope_digest(request.run_scope),
            attempt_id=request.attempt_id,
            sequence=sequence,
            payload_sha256=f"{sequence + 1:064x}",
            expected_tool_call_ids=expected_ids,
            token_weight=usage.completion_tokens,
            expires_at=NOW,
        ),
        finish_reason=finish_reason,
        actual_model="deepseek-v4-flash",
        system_fingerprint="fake-probe-v1",
        usage=usage,
        cost=cost,
        attempts=(attempt,),
    )


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"COMMERCE_AGENT_RUN_DEEPSEEK_TESTS": "1"},
        {"COMMERCE_AGENT_ACCEPT_MAX_USD": "0.01"},
    ],
)
def test_probe_preflight_requires_both_switch_and_exact_cost_acceptance(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ProbePreflightError):
        validate_probe_preflight(
            environment,
            load_reviewed_snapshots(CONFIG_ROOT),
            fixed_probe_request(CONFIG_ROOT),
        )


def test_probe_preflight_accepts_only_the_fixed_reviewed_product_request() -> None:
    snapshots = load_reviewed_snapshots(CONFIG_ROOT)
    request = fixed_probe_request(CONFIG_ROOT)

    validate_probe_preflight(paid_environment(), snapshots, request)

    tampered_payload = request.model_dump(mode="json")
    tampered_payload["run_scope"]["subject_id"] = "different-probe"
    with pytest.raises(ProbePreflightError) as caught:
        validate_probe_preflight(
            paid_environment(),
            snapshots,
            type(request).model_validate(tampered_payload),
        )
    assert caught.value.reason_code == "fixed_probe_mismatch"


def test_fixed_probe_uses_the_exclusive_v4_evidence_identity() -> None:
    request = fixed_probe_request(CONFIG_ROOT)

    assert PROBE_RUN_ID == UUID("00000000-0000-0000-0000-000000001207")
    assert PROBE_ATTEMPT_ID == UUID("00000000-0000-0000-0000-000000001208")
    assert PROBE_SUBJECT_ID == "day5-retail-probe-v5"
    assert request.run_scope.run_id == PROBE_RUN_ID
    assert request.attempt_id == PROBE_ATTEMPT_ID
    assert request.run_scope.subject_id == PROBE_SUBJECT_ID


def test_probe_profile_has_the_fixed_input_and_output_ceilings() -> None:
    profile = probe_profile(ProfileRegistry.load(CONFIG_ROOT))
    rule = next(
        rule for rule in profile.inference_rules if rule.step == "retail_decide"
    )

    assert profile.revision == "retail-probe-profile-v8"
    assert profile.input_token_limit == 4096
    assert rule.inference.max_output_tokens == 2048
    assert rule.inference.effort == "high"
    assert rule.tool_names == ("execute_readonly_sql",)
    assert probe_budget().maximum_output_tokens_per_call == 2048


def test_probe_budget_refuses_a_send_that_can_exceed_one_cent() -> None:
    budget = probe_budget()
    ledger = ProbeLedger(budget=budget, price_snapshot=peak_prices())

    ledger.reserve_send(estimated_input_tokens=4096, maximum_output_tokens=512)

    assert ledger.reserved_usd == Decimal("0.00247808")
    with pytest.raises(ProbeBudgetExceeded):
        for _ in range(4):
            ledger.reserve_send(estimated_input_tokens=4096, maximum_output_tokens=512)
    assert ledger.reserved_usd == Decimal("0.00991232")


@pytest.mark.asyncio
async def test_probe_ledger_replaces_reservation_with_validated_actual_cost() -> None:
    ledger = ProbeLedger(
        budget=ProbeBudget(
            maximum_usd=Decimal("0.01"),
            maximum_output_tokens_per_call=256,
            maximum_tool_calls=2,
            maximum_input_tokens_per_call=4096,
        ),
        price_snapshot=peak_prices(),
    )
    request = probe_model_request()
    usage = ReportedUsage(
        status="reported",
        prompt_tokens=100,
        cache_hit_tokens=0,
        cache_miss_tokens=100,
        completion_tokens=10,
        reasoning_tokens=5,
        total_tokens=110,
    )
    cost = CostEstimate(
        status="estimated",
        price_snapshot_id="probe-prices-v1",
        currency="USD",
        amount=Decimal("0.0000572"),
        price_band="peak",
    )

    await ledger.before_send(request, 1, NOW)
    await ledger.after_attempt(
        request,
        ModelAttemptSummary(
            attempt_number=1,
            sent_at=NOW,
            completed_at=NOW,
            outcome="success",
            http_status=200,
            retryable=False,
            charge_ambiguous=False,
            usage=usage,
            cost=cost,
        ),
    )

    assert ledger.reserved_usd == Decimal(0)
    assert ledger.known_cost_usd == Decimal("0.0000572")


@pytest.mark.asyncio
async def test_probe_ledger_keeps_pessimistic_reservation_when_charge_is_ambiguous() -> None:
    ledger = ProbeLedger(
        budget=ProbeBudget(
            maximum_usd=Decimal("0.01"),
            maximum_output_tokens_per_call=256,
            maximum_tool_calls=2,
            maximum_input_tokens_per_call=4096,
        ),
        price_snapshot=peak_prices(),
    )
    request = probe_model_request()
    unavailable_usage = UsageUnavailable(
        status="unavailable",
        reason_code="attempt_failed",
    )

    await ledger.before_send(request, 1, NOW)
    await ledger.after_attempt(
        request,
        ModelAttemptSummary(
            attempt_number=1,
            sent_at=NOW,
            completed_at=NOW,
            outcome="transport_error",
            retryable=True,
            charge_ambiguous=True,
            usage=unavailable_usage,
            cost=CostUnavailable(status="unavailable", reason_code="attempt_failed"),
        ),
    )

    assert ledger.reserved_usd == Decimal("0.00214016")
    assert ledger.charge_ambiguous_attempts == 1


@pytest.mark.asyncio
async def test_probe_ledger_releases_reservation_after_definite_pre_send_failure() -> None:
    ledger = ProbeLedger(
        budget=ProbeBudget(
            maximum_usd=Decimal("0.01"),
            maximum_output_tokens_per_call=256,
            maximum_tool_calls=2,
            maximum_input_tokens_per_call=4096,
        ),
        price_snapshot=peak_prices(),
    )
    request = probe_model_request()
    unavailable_usage = UsageUnavailable(
        status="unavailable",
        reason_code="attempt_failed",
    )

    await ledger.before_send(request, 1, NOW)
    await ledger.after_attempt(
        request,
        ModelAttemptSummary(
            attempt_number=1,
            sent_at=NOW,
            completed_at=NOW,
            outcome="transport_error",
            retryable=True,
            charge_ambiguous=False,
            usage=unavailable_usage,
            cost=CostUnavailable(status="unavailable", reason_code="attempt_failed"),
        ),
    )

    assert ledger.reserved_usd == Decimal(0)
    assert ledger.released_attempts == 1


@pytest.mark.asyncio
async def test_probe_fake_path_uses_one_safe_aggregate_and_private_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    sql_call = ToolCall(
        call_id="probe_call",
        name="execute_readonly_sql",
        arguments_json=json.dumps(
            {"sql": PROBE_SQL},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    gateway = FakeModel(
        [
            fake_probe_response(
                sequence=0,
                output=ToolCallOutput(type="tool_calls", tool_calls=(sql_call,)),
                finish_reason="tool_calls",
            ),
            fake_probe_response(
                sequence=1,
                output=FinalOutput(type="final", content="aggregate complete"),
                finish_reason="stop",
            ),
        ]
    )

    evidence = await run_probe(fake_settings(), probe_budget(), gateway=gateway)

    assert evidence.model_calls == 2
    assert evidence.tool_calls == 1
    assert evidence.sql_sha256 == sha256(PROBE_SQL.encode()).hexdigest()
    assert evidence.result_row_count is None
    assert evidence.total_cost_usd <= Decimal("0.01")
    assert gateway.requests[0].inference.max_output_tokens == 2048
    assert len(gateway.requests[1].history) == 1
    assert "aggregate complete" not in evidence.model_dump_json()
    output_path = tmp_path / "probe-evidence.json"
    write_probe_evidence(evidence, output_path)
    assert "aggregate complete" not in output_path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_probe_evidence(evidence, output_path)


@pytest.mark.asyncio
async def test_probe_stopped_outcome_writes_redacted_failure_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    sql_call = ToolCall(
        call_id="probe_call",
        name="execute_readonly_sql",
        arguments_json=json.dumps(
            {"sql": PROBE_SQL},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    gateway = FakeModel(
        [
            fake_probe_response(
                sequence=0,
                output=ToolCallOutput(type="tool_calls", tool_calls=(sql_call,)),
                finish_reason="tool_calls",
            ),
            fake_probe_response(
                sequence=1,
                output=FinalOutput(type="final", content="truncated private final"),
                finish_reason="length",
            ),
        ]
    )

    evidence = await run_probe(fake_settings(), probe_budget(), gateway=gateway)

    assert evidence.gate_state == "FAIL"
    assert evidence.stop_reason_code == "model_output_incomplete"
    assert evidence.model_calls == 2
    assert evidence.tool_calls == 1
    assert evidence.result_row_count is None
    output_path = tmp_path / "failed-probe-evidence.json"
    write_probe_evidence(evidence, output_path)
    payload = output_path.read_text(encoding="utf-8")
    assert "model_output_incomplete" in payload
    assert "truncated private final" not in payload


def test_cli_persists_failed_evidence_and_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "failed-probe-evidence.json"

    async def failed_probe(*args: object, **kwargs: object) -> object:
        return type("FailedEvidence", (), {"gate_state": "FAIL"})()

    def write_failed_evidence(evidence: object, path: Path) -> None:
        path.write_text(f"gate={evidence.gate_state}", encoding="utf-8")  # type: ignore[attr-defined]

    monkeypatch.setattr(probe_module, "load_settings", lambda environment: fake_settings())
    monkeypatch.setattr(probe_module, "run_probe", failed_probe)
    monkeypatch.setattr(probe_module, "write_probe_evidence", write_failed_evidence)
    monkeypatch.setattr(sys, "argv", ["probe_deepseek_gateway.py", "--output", str(output_path)])

    exit_code = probe_module.main()

    assert exit_code == 1
    assert output_path.read_text(encoding="utf-8") == "gate=FAIL"
    assert "gate=FAIL" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_probe_fake_path_rejects_any_sql_other_than_the_fixed_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    sql_call = ToolCall(
        call_id="probe_call",
        name="execute_readonly_sql",
        arguments_json='{"sql":"SELECT COUNT(*) AS order_count FROM retail.orders"}',
    )
    gateway = FakeModel(
        [
            fake_probe_response(
                sequence=0,
                output=ToolCallOutput(type="tool_calls", tool_calls=(sql_call,)),
                finish_reason="tool_calls",
            )
        ]
    )

    with pytest.raises(ProbeBudgetExceeded) as caught:
        await run_probe(fake_settings(), probe_budget(), gateway=gateway)

    assert caught.value.reason_code == "fixed_sql_mismatch"


@pytest.mark.asyncio
async def test_real_probe_path_checks_explicit_gates_before_composition() -> None:
    with pytest.raises(ProbePreflightError) as caught:
        await run_probe(
            fake_settings(),
            probe_budget(),
            environment={},
        )

    assert caught.value.reason_code == "explicit_gate_missing"


@pytest.mark.asyncio
async def test_real_probe_path_requires_all_independent_runtime_settings() -> None:
    with pytest.raises(ProbePreflightError) as caught:
        await run_probe(
            fake_settings(),
            probe_budget(),
            environment=paid_environment(),
        )

    assert caught.value.reason_code == "runtime_settings_missing"
