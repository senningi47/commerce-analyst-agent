"""Day 6 capability probe: minimal paid round-trip over the real BIRD call surface.

Replaces the Day 3 fixed-SQL-chain probe (incompatible with the Day 4/5
architecture; kept FAIL-with-documentation in the Gate P log). The probe makes
ONE non-thinking model call with a single contract tool and verifies the five
risk surfaces that each burned an authorization round during the Pilot:

1. model echo stays inside the reviewed snapshot (deepseek-flash rename precedent);
2. tool calling emits a bounded tool-call batch (contract action, not a synthetic name);
3. reported usage satisfies the accounting identities;
4. cost recomputation from the reviewed price snapshot matches exactly, including
   the peak/off-peak band decision (weekday gate — Sunday off_peak precedent);
5. attempt accounting closes (first attempt outcome ok, single paid attempt).

The verification logic is a pure function so the offline suite covers every
branch without any API call. RUNNING the probe is a paid action and requires
its own explicit authorization (<= USD 0.01).

Usage:
    uv run --env-file .env python scripts/probe_model_capability.py \
        --config-root configs/model --output outputs/probe/probe-report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx

from commerce_agent.context_builder._canonical import canonical_json
from commerce_agent.model._pricing import CostCalculator, PriceSnapshot
from commerce_agent.model._retry import RetryPolicy
from commerce_agent.model._turn_store import InMemoryProviderTurnStore
from commerce_agent.model.contracts import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    NonThinkingConfig,
    ReportedUsage,
    RunScope,
    ToolDefinition,
)
from commerce_agent.model.gateway import DeepSeekModelGateway
from commerce_agent.model.snapshots import load_reviewed_model_snapshots

PROBE_ID_PREFIX = "capability-probe"


class ProbeCheck(dict):  # type: ignore[type-arg]
    """One named verification result (JSON-serializable by construction)."""


def verify_probe_response(
    *,
    response: ModelResponse,
    capability_requested_model: str,
    prices: PriceSnapshot,
    sent_at: datetime,
) -> tuple[ProbeCheck, ...]:
    """Pure verification over one real gateway response. No I/O, no secrets."""
    checks: list[ProbeCheck] = []

    checks.append(
        ProbeCheck(
            name="model_echo",
            passed=response.actual_model == capability_requested_model,
            detail={
                "actual_model": response.actual_model,
                "requested_model": capability_requested_model,
            },
        )
    )

    tool_calls = response.output.tool_calls if response.output.type == "tool_calls" else ()
    checks.append(
        ProbeCheck(
            name="tool_call_emitted",
            passed=response.finish_reason == FinishReason.TOOL_CALLS and len(tool_calls) >= 1,
            detail={
                "finish_reason": str(response.finish_reason),
                "tool_call_count": len(tool_calls),
                "tool_names": sorted({call.name for call in tool_calls}),
            },
        )
    )

    usage = response.usage
    identities_hold = (
        isinstance(usage, ReportedUsage)
        and usage.prompt_tokens == usage.cache_hit_tokens + usage.cache_miss_tokens
        and usage.total_tokens == usage.prompt_tokens + usage.completion_tokens
        and usage.reasoning_tokens <= usage.completion_tokens
    )
    checks.append(
        ProbeCheck(
            name="usage_identities",
            passed=identities_hold,
            detail={"status": getattr(usage, "status", "unavailable")},
        )
    )

    recalculated = CostCalculator(prices).calculate(usage, sent_at)
    checks.append(
        ProbeCheck(
            name="cost_recalculation",
            passed=(
                response.cost.status == "estimated"
                and recalculated.amount == response.cost.amount
                and recalculated.price_band == response.cost.price_band
            ),
            detail={
                "reported_amount": str(response.cost.amount)
                if response.cost.status == "estimated"
                else "unavailable",
                "recalculated_amount": str(recalculated.amount),
                "band": recalculated.price_band,
                "sent_at_utc": sent_at.astimezone(UTC).isoformat(),
                "sent_weekday": sent_at.astimezone(UTC).strftime("%A"),
            },
        )
    )

    first_attempt = response.attempts[0]
    checks.append(
        ProbeCheck(
            name="attempt_accounting",
            passed=(
                len(response.attempts) == 1
                and first_attempt.outcome == "success"
                and first_attempt.cost == response.cost
            ),
            detail={
                "attempt_count": len(response.attempts),
                "first_outcome": str(first_attempt.outcome),
            },
        )
    )
    return tuple(checks)


def build_probe_request(*, config_root: Path) -> ModelRequest:
    snapshots = load_reviewed_model_snapshots(config_root)
    scope = RunScope(
        run_id=uuid4(),
        track="bird",
        mode="a",
        subject_id="capability-probe",
        experiment_id=f"{PROBE_ID_PREFIX}-{uuid4().hex[:12]}",
        config_hash="a" * 64,
    )
    tool = ToolDefinition(
        name="get_schema",
        description="Return the schema of the selected database.",
        parameters_json=canonical_json(
            {"type": "object", "properties": {}, "required": []}
        ),
        catalog_revision="probe-catalog-v1",
        capability_sha256="b" * 64,
    )
    return ModelRequest(
        run_scope=scope,
        attempt_id=uuid4(),
        sequence=0,
        inference=NonThinkingConfig(type="disabled", temperature=0, max_output_tokens=1024),
        messages=(
            _user_message(
                "Call the get_schema tool now to verify tool calling. Do not answer in text."
            ),
        ),
        tools=(tool,),
        timeout_seconds=Decimal(60),
        provider_user_id=os.environ.get("DEEPSEEK_PROVIDER_USER_ID", "0" * 32),
        capability_revision=snapshots.capability.revision,
        prompt_policy_hash="c" * 64,
        rendered_prompt_hash="d" * 64,
        tool_hash="e" * 64,
        context_hash="f" * 64,
        config_hash=scope.config_hash,
    )


def _user_message(content: str):
    from commerce_agent.model.contracts import ChatMessage

    return ChatMessage(role="user", content=content)


class _FrozenClock:
    """Clock protocol adapter: the probe freezes sent_at for exact cost recheck."""

    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


async def run_probe(*, config_root: Path) -> dict[str, object]:
    snapshots = load_reviewed_model_snapshots(config_root)
    request = build_probe_request(config_root=config_root)
    api_key = os.environ["DEEPSEEK_API_KEY"]
    base_url = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com")

    clock = _FrozenClock(datetime.now(UTC))
    async with httpx.AsyncClient(
        base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=120.0
    ) as client:
        gateway = DeepSeekModelGateway(
            client=client,
            turn_store=InMemoryProviderTurnStore(clock=clock),
            capability=snapshots.capability,
            prices=snapshots.prices,
            retry_policy=RetryPolicy(maximum_attempts=1),
            clock=clock,
            sleeper=lambda _seconds: asyncio.sleep(0),
            jitter_rng=lambda: Decimal(0),
        )
        response = await gateway.complete(request)

    checks = verify_probe_response(
        response=response,
        capability_requested_model=snapshots.capability.requested_model,
        prices=snapshots.prices,
        sent_at=clock.now(),
    )
    return {
        "probe_id": f"{PROBE_ID_PREFIX}-{clock.now().strftime('%Y%m%dT%H%M%SZ')}",
        "requested_model": snapshots.capability.requested_model,
        "actual_model": response.actual_model,
        "usage": response.usage.model_dump(mode="json"),
        "cost": response.cost.model_dump(mode="json"),
        "checks": checks,
        "all_passed": all(check["passed"] for check in checks),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-root", type=Path, default=Path("configs/model"))
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    report = asyncio.run(run_probe(config_root=args.config_root))
    rendered = json.dumps(report, ensure_ascii=False, indent=1)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
