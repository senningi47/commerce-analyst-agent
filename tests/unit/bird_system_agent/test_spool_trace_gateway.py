"""Unit tests for the agent-side SpoolTraceGateway (v0.3 §18 agent-visible track).

The gateway wraps the ModelGateway and appends one public JSONL event per
model turn to the attempt's spool file; usage/cost/hashes never enter
responses, they flow only through this channel.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from bird_system_agent.runtime import SpoolTraceGateway
from commerce_agent.context_builder._tokens import TokenEstimate
from commerce_agent.context_builder.builder import (
    ContextBuilder,
    ContextRequest,
    compute_config_hash,
    scope_digest,
)
from commerce_agent.context_builder.contracts import ContextDatum
from commerce_agent.context_builder.profiles import ProfileRegistry
from commerce_agent.model.contracts import (
    CostUnavailable,
    FinalOutput,
    ModelAttemptSummary,
    ModelRequest,
    ModelResponse,
    ProviderTurnRef,
    ReportedUsage,
    RunScope,
    UsageUnavailable,
)
from commerce_agent.model.fake import FakeModel

CONFIG_ROOT = Path(__file__).parents[3] / "configs" / "model"
RUN_ID = UUID("00000000-0000-0000-0000-000000000911")
ATTEMPT_ID = UUID("00000000-0000-0000-0000-000000000912")
OTHER_ATTEMPT_ID = UUID("00000000-0000-0000-0000-000000000913")
NOW = datetime(2026, 9, 12, 3, 0, tzinfo=UTC)


class FixedEstimator:
    @property
    def revision(self) -> str:
        return "deepseek-tokenizer-v1"

    def estimate(self, request: object) -> TokenEstimate:
        del request
        return TokenEstimate(input_tokens=100, estimator_revision=self.revision)


def _context_builder() -> ContextBuilder:
    return ContextBuilder(
        registry=ProfileRegistry.load(CONFIG_ROOT),
        estimator=FixedEstimator(),
        requested_model="deepseek-chat",
        timeout_seconds=Decimal(30),
        provider_user_id="b" * 32,
    )


def _bundle_request(attempt_id: UUID, *, sequence: int) -> ModelRequest:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    profile = registry.get("bird_a")
    inference = next(rule.inference for rule in profile.inference_rules)
    scope = RunScope(
        run_id=RUN_ID,
        track="bird",
        mode="a",
        subject_id="spool-a",
        experiment_id="day5",
        config_hash=compute_config_hash(profile, inference, "deepseek-chat"),
    )
    content = "spool trace probe"
    bundle = _context_builder().build(
        ContextRequest(
            run_scope=scope,
            attempt_id=attempt_id,
            sequence=sequence,
            profile=profile,
            step="bird_a_act",
            current_input=ContextDatum(
                kind="user_input",
                namespace="user_input",
                source_ref="synthetic:request",
                revision=None,
                content=content,
                digest=sha256(content.encode("utf-8")).hexdigest(),
            ),
            confirmed_facts=(),
            evidence=(),
            latest_error=None,
            history=(),
        )
    )
    return bundle.model_request


def _response(
    scope: RunScope,
    *,
    sequence: int,
    output: FinalOutput,
    usage: ReportedUsage | UsageUnavailable | None = None,
) -> ModelResponse:
    resolved_usage = usage or ReportedUsage(
        status="reported",
        prompt_tokens=10,
        cache_hit_tokens=4,
        cache_miss_tokens=6,
        completion_tokens=2,
        reasoning_tokens=0,
        total_tokens=12,
    )
    cost = CostUnavailable(status="unavailable", reason_code="fixture_price_unavailable")
    attempt = ModelAttemptSummary(
        attempt_number=1,
        sent_at=NOW,
        completed_at=NOW,
        outcome="success",
        http_status=200,
        retryable=False,
        charge_ambiguous=False,
        usage=resolved_usage,
        cost=cost,
    )
    return ModelResponse(
        output=output,
        provider_turn_ref=ProviderTurnRef(
            turn_id=UUID(int=sequence + 1),
            scope_digest=scope_digest(scope),
            attempt_id=ATTEMPT_ID,
            sequence=sequence,
            payload_sha256=f"{sequence + 1:064x}",
            expected_tool_call_ids=(),
            token_weight=2,
            expires_at=NOW,
        ),
        finish_reason="stop",
        actual_model="fake",
        system_fingerprint="fake-v1",
        usage=resolved_usage,
        cost=cost,
        attempts=(attempt,),
    )


async def test_spool_gateway_writes_public_event_per_model_turn(tmp_path: Path) -> None:
    request = _bundle_request(ATTEMPT_ID, sequence=0)
    inner = FakeModel(
        [_response(request.run_scope, sequence=0, output=FinalOutput(type="final", content="SELECT 1"))]
    )
    gateway = SpoolTraceGateway(inner, tmp_path / "spool")

    response = await gateway.complete(request)

    assert response.output == FinalOutput(type="final", content="SELECT 1")
    path = tmp_path / "spool" / f"{ATTEMPT_ID}.jsonl"
    assert path.is_file()
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    event = events[0]
    assert event["run_scope_digest"] == scope_digest(request.run_scope)
    assert event["attempt_id"] == str(ATTEMPT_ID)
    assert event["sequence"] == 0
    assert event["event_type"] == "model_turn"
    payload = event["payload"]
    assert payload["actual_model"] == "fake"
    assert payload["usage"]["status"] == "reported"
    assert payload["usage"]["prompt_tokens"] == 10
    assert payload["usage"]["cache_hit_tokens"] == 4
    assert payload["cost"]["status"] == "unavailable"
    assert len(inner.requests) == 1


async def test_spool_gateway_keys_files_per_attempt_in_sequence_order(tmp_path: Path) -> None:
    first = _bundle_request(ATTEMPT_ID, sequence=0)
    second = _bundle_request(ATTEMPT_ID, sequence=1)
    other = _bundle_request(OTHER_ATTEMPT_ID, sequence=0)
    inner = FakeModel(
        [
            _response(first.run_scope, sequence=0, output=FinalOutput(type="final", content="a")),
            _response(first.run_scope, sequence=1, output=FinalOutput(type="final", content="b")),
            _response(other.run_scope, sequence=0, output=FinalOutput(type="final", content="c")),
        ]
    )
    gateway = SpoolTraceGateway(inner, tmp_path / "spool")

    await gateway.complete(first)
    await gateway.complete(second)
    await gateway.complete(other)

    own_lines = (tmp_path / "spool" / f"{ATTEMPT_ID}.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["sequence"] for line in own_lines] == [0, 1]
    other_path = tmp_path / "spool" / f"{OTHER_ATTEMPT_ID}.jsonl"
    assert other_path.is_file()
    other_event = json.loads(other_path.read_text(encoding="utf-8").splitlines()[0])
    assert other_event["attempt_id"] == str(OTHER_ATTEMPT_ID)


async def test_spool_gateway_records_unavailable_usage_without_crashing(tmp_path: Path) -> None:
    request = _bundle_request(ATTEMPT_ID, sequence=0)
    inner = FakeModel(
        [
            _response(
                request.run_scope,
                sequence=0,
                output=FinalOutput(type="final", content="SELECT 2"),
                usage=UsageUnavailable(status="unavailable", reason_code="fixture_missing_usage"),
            )
        ]
    )
    gateway = SpoolTraceGateway(inner, tmp_path / "spool")

    await gateway.complete(request)

    event = json.loads(
        (tmp_path / "spool" / f"{ATTEMPT_ID}.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert event["payload"]["usage"] == {
        "status": "unavailable",
        "reason_code": "fixture_missing_usage",
    }
