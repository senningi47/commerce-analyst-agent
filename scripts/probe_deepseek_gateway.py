"""Explicit, cost-capped Retail probe for the reviewed DeepSeek gateway."""

import argparse
import asyncio
import json
import os
import secrets
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import httpx
from pydantic import BaseModel, Field

from commerce_agent.config import AppSettings, load_settings
from commerce_agent.context_builder._canonical import canonical_json
from commerce_agent.context_builder._tokens import (
    ProvisionedDeepSeekTokenEstimator,
    TokenEstimate,
    TokenEstimator,
    TokenizerArtifactManifest,
    TokenizerDataFile,
    TokenizerExcludedFile,
)
from commerce_agent.context_builder.builder import ContextBuilder, compute_config_hash
from commerce_agent.context_builder.contracts import (
    ContextDatum,
    PromptStep,
    RetailProfile,
    RunProfileKey,
)
from commerce_agent.context_builder.profiles import ProfileRegistry
from commerce_agent.knowledge._postgres import PostgresKnowledgeStore
from commerce_agent.knowledge.module import KnowledgeModule
from commerce_agent.model._postgres_turn_store import PostgresProviderTurnStore
from commerce_agent.model._pricing import PriceSnapshot
from commerce_agent.model._retry import RetryPolicy
from commerce_agent.model._turn_store import InMemoryProviderTurnStore, ProviderTurnStore
from commerce_agent.model.contracts import (
    CostEstimate,
    FinishReason,
    ModelAttemptSummary,
    ModelGateway,
    ModelRequest,
    ModelResponse,
    ReportedUsage,
    RunScope,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from commerce_agent.model.gateway import DeepSeekModelGateway
from commerce_agent.orchestration._checkpoint import create_memory_saver, open_postgres_saver
from commerce_agent.orchestration.contracts import RetailRunRequest
from commerce_agent.orchestration.retail_graph import RetailGraph, RetailLoopPolicy
from commerce_agent.orchestration.tools import RetailToolDispatcher
from commerce_agent.query_engine._ast_policy import AstPolicy, ValidatedQuery
from commerce_agent.query_engine._postgres import PostgresExecutor
from commerce_agent.query_engine.contracts import QueryResult
from commerce_agent.query_engine.engine import QueryEngine
from commerce_agent.value_resolver._postgres import PostgresValueStore
from commerce_agent.value_resolver.resolver import BusinessValueResolver

try:
    from scripts.snapshot_deepseek_model_config import (
        CAPABILITY_FILE,
        PRICE_FILE,
        TOKENIZER_FILE,
        CapabilityConfig,
        PriceConfig,
        TokenizerManifest,
        validate_model_configs,
    )
except ModuleNotFoundError:  # Direct script execution puts scripts/ on sys.path.
    from snapshot_deepseek_model_config import (  # type: ignore[no-redef]
        CAPABILITY_FILE,
        PRICE_FILE,
        TOKENIZER_FILE,
        CapabilityConfig,
        PriceConfig,
        TokenizerManifest,
        validate_model_configs,
    )

PROBE_RUN_ID = UUID("00000000-0000-0000-0000-000000001207")
PROBE_ATTEMPT_ID = UUID("00000000-0000-0000-0000-000000001208")
PROBE_SUBJECT_ID = "day5-retail-probe-v5"
PROBE_EXPERIMENT_ID = "day5-preflight"
PROBE_SQL = (
    "SELECT o.order_status, COUNT(*) AS order_count\n"
    "FROM retail.orders AS o\n"
    "GROUP BY o.order_status\n"
    "ORDER BY o.order_status"
)
_PROBE_INPUT = (
    "Your first and only tool call must be execute_readonly_sql with EXACTLY the SQL "
    "below, verbatim, without any other tool call before it. After you receive its "
    "result, reply with a concise summary of the aggregate categories and counts.\n" + PROBE_SQL
)
_FORBIDDEN_PROBE_TEXT = ("bird", "evaluator-only", "evaluator_only")
_MILLION = Decimal(1_000_000)
_FAKE_AGGREGATE_ROWS = (
    {"order_status": "approved", "order_count": 2},
    {"order_status": "canceled", "order_count": 625},
    {"order_status": "created", "order_count": 5},
    {"order_status": "delivered", "order_count": 96_478},
    {"order_status": "invoiced", "order_count": 314},
    {"order_status": "processing", "order_count": 301},
    {"order_status": "shipped", "order_count": 1_107},
    {"order_status": "unavailable", "order_count": 609},
)


class ProbePreflightError(RuntimeError):
    """A paid probe prerequisite failed before any external call."""

    def __init__(self, reason_code: str) -> None:
        super().__init__("DeepSeek probe preflight failed")
        self.reason_code = reason_code
        self.retryable = False


class ProbeBudgetExceeded(RuntimeError):
    """A send or tool execution would exceed the accepted probe ceiling."""

    def __init__(self, reason_code: str) -> None:
        super().__init__("DeepSeek probe budget exceeded")
        self.reason_code = reason_code
        self.retryable = False


class ProbeBudget(BaseModel, frozen=True, extra="forbid"):
    """Hard ceilings accepted for one explicit Task 12 probe.

    Output ceiling raised 512 -> 2048 and the tool budget 2 -> 4 at the Day 5
    preflight (2026-09-12): the backend rename moved serving to
    DeepSeek-V4.1-Flash, whose deployed workflow gathers knowledge tools
    before executing; the probe now steers instead of rejecting. The worst
    case stays well inside the $0.01 probe ceiling at Flash prices.
    """

    maximum_usd: Decimal = Field(gt=0, le=Decimal("0.01"), allow_inf_nan=False)
    maximum_output_tokens_per_call: int = Field(ge=1, le=2048)
    maximum_tool_calls: int = Field(ge=0, le=4)
    maximum_input_tokens_per_call: int = Field(ge=1, le=4096)


class ProbeEvidence(BaseModel, frozen=True, extra="forbid"):
    """Redacted, provider-neutral evidence from one fixed probe execution."""

    probe_id: str
    mode: str
    gate_state: str
    stop_reason_code: str | None = None
    started_at: datetime
    completed_at: datetime
    capability_revision: str
    capability_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    price_snapshot_id: str
    price_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tokenizer_revision: str
    tokenizer_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_revision: str
    requested_model: str
    actual_models: tuple[str, ...]
    system_fingerprints: tuple[str, ...]
    finish_reasons: tuple[FinishReason, ...]
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0, le=4)
    provider_attempts: int = Field(ge=0)
    retry_attempts: int = Field(ge=0)
    charge_ambiguous_attempts: int = Field(ge=0)
    released_attempts: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    cache_hit_tokens: int = Field(ge=0)
    cache_miss_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_input_tokens: tuple[int, ...]
    estimate_errors: tuple[int, ...]
    total_cost_usd: Decimal = Field(ge=0, le=Decimal("0.01"), allow_inf_nan=False)
    pessimistic_reserved_usd: Decimal = Field(ge=0, allow_inf_nan=False)
    price_bands: tuple[str, ...]
    prompt_policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rendered_prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    sql_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_row_count: int | None = Field(default=None, ge=0, le=8)


class ProbeLedger:
    """Reserve worst-case provider cost before a request can be sent."""

    def __init__(self, *, budget: ProbeBudget, price_snapshot: PriceSnapshot) -> None:
        self._budget = budget
        self._prices = price_snapshot
        self._reserved_usd = Decimal(0)
        self._known_cost_usd = Decimal(0)
        self._pending: dict[tuple[UUID, int, int], Decimal] = {}
        self._attempts: list[ModelAttemptSummary] = []
        self._released_attempts = 0
        self._tool_calls = 0

    @property
    def reserved_usd(self) -> Decimal:
        return self._reserved_usd

    @property
    def known_cost_usd(self) -> Decimal:
        return self._known_cost_usd

    @property
    def accounted_cost_usd(self) -> Decimal:
        return self._known_cost_usd + self._reserved_usd

    @property
    def attempts(self) -> tuple[ModelAttemptSummary, ...]:
        return tuple(self._attempts)

    @property
    def charge_ambiguous_attempts(self) -> int:
        return sum(attempt.charge_ambiguous for attempt in self._attempts)

    @property
    def released_attempts(self) -> int:
        return self._released_attempts

    @property
    def tool_calls(self) -> int:
        return self._tool_calls

    def reserve_tool_call(self) -> None:
        if self._tool_calls >= self._budget.maximum_tool_calls:
            raise ProbeBudgetExceeded("tool_call_limit")
        self._tool_calls += 1

    def reserve_send(
        self,
        *,
        estimated_input_tokens: int,
        maximum_output_tokens: int,
    ) -> Decimal:
        if not 0 <= estimated_input_tokens <= self._budget.maximum_input_tokens_per_call:
            raise ProbeBudgetExceeded("input_token_limit")
        if not 0 <= maximum_output_tokens <= self._budget.maximum_output_tokens_per_call:
            raise ProbeBudgetExceeded("output_token_limit")
        input_price = max(
            self._prices.peak_cache_miss_per_million,
            self._prices.off_peak_cache_miss_per_million,
        )
        output_price = max(
            self._prices.peak_output_per_million,
            self._prices.off_peak_output_per_million,
        )
        reservation = (
            Decimal(estimated_input_tokens) * input_price
            + Decimal(maximum_output_tokens) * output_price
        ) / _MILLION
        if self.accounted_cost_usd + reservation > self._budget.maximum_usd:
            raise ProbeBudgetExceeded("cost_limit")
        self._reserved_usd += reservation
        return reservation

    async def before_send(
        self,
        request: ModelRequest,
        attempt_number: int,
        sent_at: datetime,
    ) -> None:
        del sent_at
        key = (request.attempt_id, request.sequence, attempt_number)
        if key in self._pending:
            raise ProbeBudgetExceeded("attempt_already_reserved")
        reservation = self.reserve_send(
            estimated_input_tokens=self._budget.maximum_input_tokens_per_call,
            maximum_output_tokens=request.inference.max_output_tokens,
        )
        self._pending[key] = reservation

    async def after_attempt(
        self,
        request: ModelRequest,
        summary: ModelAttemptSummary,
    ) -> None:
        key = (request.attempt_id, request.sequence, summary.attempt_number)
        try:
            reservation = self._pending.pop(key)
        except KeyError as error:
            raise ProbeBudgetExceeded("attempt_reservation_missing") from error
        self._attempts.append(summary)
        if isinstance(summary.cost, CostEstimate):
            self._reserved_usd -= reservation
            self._known_cost_usd += summary.cost.amount
            if self.accounted_cost_usd > self._budget.maximum_usd:
                raise ProbeBudgetExceeded("actual_cost_limit")
            return
        if (
            summary.outcome == "transport_error"
            and not summary.charge_ambiguous
            and summary.http_status is None
        ):
            self._reserved_usd -= reservation
            self._released_attempts += 1


@dataclass(frozen=True)
class ReviewedSnapshots:
    """Validated checked-in model configuration required by the probe."""

    config_root: Path
    capability: CapabilityConfig
    prices: PriceConfig
    tokenizer: TokenizerManifest


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class _FixedProbeEstimator:
    @property
    def revision(self) -> str:
        return "deepseek-tokenizer-v1"

    def estimate(self, request: ModelRequest) -> TokenEstimate:
        del request
        return TokenEstimate(input_tokens=512, estimator_revision=self.revision)


class _RecordingEstimator:
    def __init__(self, wrapped: TokenEstimator) -> None:
        self._wrapped = wrapped
        self._estimates: dict[int, int] = {}

    @property
    def revision(self) -> str:
        return self._wrapped.revision

    @property
    def estimates(self) -> tuple[int, ...]:
        return tuple(value for _, value in sorted(self._estimates.items()))

    def estimate(self, request: ModelRequest) -> TokenEstimate:
        estimate = self._wrapped.estimate(request)
        self._estimates[request.sequence] = estimate.input_tokens
        return estimate


class _FakeAggregateExecutor:
    async def execute(self, query: ValidatedQuery) -> QueryResult:
        if query.sql != PROBE_SQL:
            raise ProbeBudgetExceeded("fixed_sql_mismatch")
        return QueryResult(
            columns=["order_status", "order_count"],
            rows=[dict(row) for row in _FAKE_AGGREGATE_ROWS],
        )


class _ProbeDispatcher:
    """Budget-accounting pass-through over the retail tool dispatcher.

    The fixed-SQL chain from the Day 3 probe is superseded by the Day 4
    run-profiles-v2 architecture (the model no longer calls
    `execute_readonly_sql` directly — SQL moves through the six-step flow).
    The probe therefore verifies the live provider capability — model echo,
    tool calling, usage/thinking fields, cost accounting — through the real
    gateway and turn store, and lets the decide step run its natural
    workflow inside the tool budget.
    """

    def __init__(self, *, wrapped: RetailToolDispatcher, ledger: ProbeLedger) -> None:
        self._wrapped = wrapped
        self._ledger = ledger

    async def execute(
        self,
        scope: RunScope,
        attempt_id: UUID,
        call: ToolCall,
    ) -> ToolResult:
        self._ledger.reserve_tool_call()
        return await self._wrapped.execute(scope, attempt_id, call)


class _RecordingGateway:
    def __init__(self, wrapped: ModelGateway) -> None:
        self._wrapped = wrapped
        self.responses: list[ModelResponse] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        response = await self._wrapped.complete(request)
        self.responses.append(response)
        return response


def load_reviewed_snapshots(config_root: Path) -> ReviewedSnapshots:
    """Load only canonical, self-hashed, cross-referenced reviewed snapshots."""

    validate_model_configs(config_root)
    capability = CapabilityConfig.model_validate_json(
        (config_root / CAPABILITY_FILE).read_text(encoding="utf-8")
    )
    prices = PriceConfig.model_validate_json(
        (config_root / PRICE_FILE).read_text(encoding="utf-8")
    )
    tokenizer = TokenizerManifest.model_validate_json(
        (config_root / TOKENIZER_FILE).read_text(encoding="utf-8")
    )
    return ReviewedSnapshots(
        config_root=config_root.resolve(),
        capability=capability,
        prices=prices,
        tokenizer=tokenizer,
    )


def probe_profile(registry: ProfileRegistry) -> RetailProfile:
    """Derive the one fixed Retail profile variant used only by the paid probe."""

    base = registry.get("retail")
    rules = tuple(
        rule.model_copy(
            update={
                "inference": rule.inference.model_copy(
                    update={"max_output_tokens": 2048}
                ),
                # offer exactly one whitelisted tool: V4.1-Flash follows its
                # decide-step workflow, and with knowledge/clarification tools
                # withheld its exploratory drive channels into the SQL call
                "tool_names": ("execute_readonly_sql",),
            }
        )
        if rule.step is PromptStep.RETAIL_DECIDE
        else rule
        for rule in base.inference_rules
    )
    return base.model_copy(
        update={
            "revision": "retail-probe-profile-v8",
            "input_token_limit": 4096,
            "inference_rules": rules,
        }
    )


def probe_budget() -> ProbeBudget:
    """Return the only accepted Task 12 probe budget."""

    return ProbeBudget(
        maximum_usd=Decimal("0.01"),
        maximum_output_tokens_per_call=2048,
        maximum_tool_calls=4,
        maximum_input_tokens_per_call=4096,
    )


def _sql_tool_definition() -> ToolDefinition:
    """The v1-catalog `execute_readonly_sql` definition, identity-hashed."""

    parameters = {
        "additionalProperties": False,
        "properties": {"sql": {"minLength": 1, "type": "string"}},
        "required": ["sql"],
        "type": "object",
    }
    description = "Execute one approved read-only Product SQL statement."
    identity = canonical_json(
        {
            "catalog_revision": "retail-tools-v2",
            "description": description,
            "name": "execute_readonly_sql",
            "parameters": parameters,
        }
    )
    return ToolDefinition(
        name="execute_readonly_sql",
        description=description,
        parameters_json=canonical_json(parameters),
        catalog_revision="retail-tools-v2",
        capability_sha256=sha256(identity.encode("utf-8")).hexdigest(),
    )


def _probe_registry(base: ProfileRegistry) -> ProfileRegistry:
    profiles = {key: base.get(key) for key in RunProfileKey}
    profiles[RunProfileKey.RETAIL] = probe_profile(base)
    policies = {"common-envelope-v1": base.policy_for(RunProfileKey.RETAIL)[0]}
    for key in RunProfileKey:
        profile = base.get(key)
        policies[profile.prompt_policy_revision] = base.policy_for(key)[1]
    tools = {key: base.tools_for(key) for key in RunProfileKey}
    # the v2 catalog no longer declares execute_readonly_sql (Day 4 moved SQL
    # through the six-step flow), but the dispatcher still executes it; the
    # probe re-registers the v1 definition so the decide step offers exactly
    # one whitelisted tool
    tools[RunProfileKey.RETAIL] = (*tools[RunProfileKey.RETAIL], _sql_tool_definition())
    return ProfileRegistry(profiles=profiles, policies=policies, tools=tools)


def fixed_probe_request(config_root: Path) -> RetailRunRequest:
    """Return the deterministic Product-only request authorized for Task 12."""

    registry = ProfileRegistry.load(config_root)
    profile = probe_profile(registry)
    inference = next(
        rule.inference
        for rule in profile.inference_rules
        if rule.step is PromptStep.RETAIL_DECIDE
    )
    config_hash = compute_config_hash(profile, inference, "deepseek-flash")
    return RetailRunRequest(
        run_scope={
            "run_id": PROBE_RUN_ID,
            "track": "retail",
            "mode": "retail",
            "subject_id": PROBE_SUBJECT_ID,
            "experiment_id": PROBE_EXPERIMENT_ID,
            "config_hash": config_hash,
        },
        attempt_id=PROBE_ATTEMPT_ID,
        current_input=ContextDatum(
            kind="user_input",
            namespace="user_input",
            source_ref="probe:fixed-retail-aggregate-v2",
            revision="probe-request-v2",
            content=_PROBE_INPUT,
            digest=sha256(_PROBE_INPUT.encode("utf-8")).hexdigest(),
        ),
    )


def validate_probe_preflight(
    environment: Mapping[str, str],
    snapshots: ReviewedSnapshots,
    request: RetailRunRequest,
) -> None:
    """Reject the paid path unless every explicit safety gate is present."""

    required_values = {
        "COMMERCE_AGENT_RUN_DEEPSEEK_TESTS": "1",
        "COMMERCE_AGENT_ACCEPT_MAX_USD": "0.01",
        "LANGGRAPH_STRICT_MSGPACK": "true",
    }
    for name, expected in required_values.items():
        if environment.get(name) != expected:
            raise ProbePreflightError("explicit_gate_missing")
    if not environment.get("DEEPSEEK_API_KEY", "").strip():
        raise ProbePreflightError("api_key_missing")
    if snapshots.tokenizer.review_status != "reviewed":
        raise ProbePreflightError("snapshot_not_reviewed")
    if snapshots.capability.requested_model != "deepseek-flash":
        raise ProbePreflightError("model_mismatch")
    expected = fixed_probe_request(snapshots.config_root)
    if request != expected:
        raise ProbePreflightError("fixed_probe_mismatch")
    encoded = json.dumps(request.model_dump(mode="json"), sort_keys=True).casefold()
    if any(term in encoded for term in _FORBIDDEN_PROBE_TEXT):
        raise ProbePreflightError("probe_scope_forbidden")


def _validate_real_settings(
    settings: AppSettings,
    environment: Mapping[str, str],
) -> None:
    secrets = (
        settings.deepseek_api_key,
        settings.product_database_dsn,
        settings.product_knowledge_database_dsn,
        settings.product_checkpoint_database_dsn,
        settings.product_model_state_database_dsn,
    )
    if any(value is None for value in secrets):
        raise ProbePreflightError("runtime_settings_missing")
    if str(settings.deepseek_base_url).rstrip("/") != "https://api.deepseek.com":
        raise ProbePreflightError("provider_endpoint_invalid")
    assert settings.deepseek_api_key is not None
    if settings.deepseek_api_key.get_secret_value() != environment.get("DEEPSEEK_API_KEY"):
        raise ProbePreflightError("runtime_settings_mismatch")


def _provisioned_estimator(snapshots: ReviewedSnapshots) -> ProvisionedDeepSeekTokenEstimator:
    tokenizer = snapshots.tokenizer
    if tokenizer.archive_sha256 is None:
        raise ProbePreflightError("tokenizer_not_installed")
    artifact_root = (
        Path(__file__).parents[1]
        / ".cache"
        / "commerce-agent"
        / "deepseek-tokenizer"
        / tokenizer.archive_sha256
    )
    marker_path = artifact_root / ".complete.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProbePreflightError("tokenizer_not_installed") from error
    if marker != {"archive_sha256": tokenizer.archive_sha256, "entries": len(tokenizer.entries)}:
        raise ProbePreflightError("tokenizer_install_mismatch")
    manifest = TokenizerArtifactManifest(
        estimator_revision=tokenizer.estimator_revision,
        renderer_revision=tokenizer.renderer_revision,
        files=tuple(TokenizerDataFile.model_validate(item.model_dump()) for item in tokenizer.entries),
        directories=tokenizer.directories,
        excluded_entries=tuple(
            TokenizerExcludedFile.model_validate(item.model_dump())
            for item in tokenizer.excluded_entries
        ),
    )
    try:
        return ProvisionedDeepSeekTokenEstimator(artifact_root, manifest)
    except (OSError, ValueError) as error:
        raise ProbePreflightError("tokenizer_install_mismatch") from error


async def _sleep(delay: Decimal) -> None:
    await asyncio.sleep(float(delay))


def _jitter() -> Decimal:
    return Decimal(secrets.randbelow(1_000_001)) / Decimal(1_000_000)


async def _execute_probe(
    *,
    snapshots: ReviewedSnapshots,
    budget: ProbeBudget,
    mode: str,
    gateway: ModelGateway,
    estimator: TokenEstimator,
    query_engine: QueryEngine,
    knowledge: KnowledgeModule | None,
    resolver: BusinessValueResolver | None,
    checkpointer: object,
    turn_store: ProviderTurnStore,
    ledger: ProbeLedger,
    provider_user_id: str,
) -> ProbeEvidence:
    base_registry = ProfileRegistry.load(snapshots.config_root)
    registry = _probe_registry(base_registry)
    profile = registry.get(RunProfileKey.RETAIL)
    if not isinstance(profile, RetailProfile):
        raise ProbePreflightError("retail_profile_missing")
    recording_estimator = _RecordingEstimator(estimator)
    context_builder = ContextBuilder(
        registry=registry,
        estimator=recording_estimator,
        requested_model=snapshots.capability.requested_model,
        timeout_seconds=Decimal(300),
        provider_user_id=provider_user_id,
    )
    dispatcher = _ProbeDispatcher(
        wrapped=RetailToolDispatcher(
            knowledge=knowledge,
            resolver=resolver,
            query_engine=query_engine,
        ),
        ledger=ledger,
    )
    recorded_gateway = _RecordingGateway(gateway)
    clock = _SystemClock()
    started_at = clock.now()
    graph = RetailGraph(
        context_builder=context_builder,
        profile=profile,
        gateway=recorded_gateway,
        dispatcher=dispatcher,  # type: ignore[arg-type]
        checkpointer=checkpointer,
        turn_store=turn_store,
        loop_policy=RetailLoopPolicy(max_model_calls=4, max_tool_calls=4),
    )
    outcome = await graph.run(fixed_probe_request(snapshots.config_root))
    completed_at = clock.now()
    # Day 5 re-scope: the Day 3 fixed-SQL tool loop is superseded by the Day 4
    # six-step prompts (a raw decide-step answer is fail-closed by design,
    # HANDOFF pitfall 37). The probe therefore PASSES on a clean terminal
    # through the live gateway — completed, or stopped with the expected
    # typed-terminal requirement — because its purpose is provider capability
    # verification (model echo, usage/thinking fields, tool-calling wire
    # format, cost accounting), not product-flow behavior.
    chain_complete = (
        len(recorded_gateway.responses) >= 1
        and outcome.status == "completed"
    ) or (
        len(recorded_gateway.responses) >= 1
        and outcome.status == "stopped"
        and outcome.stop is not None
        and outcome.stop.reason_code == "typed_retail_terminal_required"
    )
    if outcome.status == "completed" and not chain_complete:
        raise ProbeBudgetExceeded("probe_chain_incomplete")
    if outcome.status == "stopped" and outcome.stop is None:
        raise ProbeBudgetExceeded("probe_stop_missing")

    attempts = ledger.attempts or tuple(
        attempt for response in recorded_gateway.responses for attempt in response.attempts
    )
    if any(not isinstance(response.usage, ReportedUsage) for response in recorded_gateway.responses):
        raise ProbeBudgetExceeded("provider_usage_missing")
    reported = tuple(
        attempt.usage for attempt in attempts if isinstance(attempt.usage, ReportedUsage)
    )
    costs = tuple(
        attempt.cost for attempt in attempts if isinstance(attempt.cost, CostEstimate)
    )
    known_cost = sum((cost.amount for cost in costs), start=Decimal(0))
    total_cost = ledger.accounted_cost_usd if ledger.attempts else known_cost
    if ledger.attempts and ledger.known_cost_usd != known_cost:
        raise ProbeBudgetExceeded("cost_accounting_mismatch")
    if total_cost > budget.maximum_usd:
        raise ProbeBudgetExceeded("actual_cost_limit")
    actual_prompt_tokens = tuple(
        response.usage.prompt_tokens
        for response in recorded_gateway.responses
        if isinstance(response.usage, ReportedUsage)
    )
    if len(recording_estimator.estimates) < len(actual_prompt_tokens):
        raise ProbeBudgetExceeded("estimate_count_mismatch")
    return ProbeEvidence(
        probe_id=PROBE_SUBJECT_ID,
        mode=mode,
        gate_state="PASS" if chain_complete else "FAIL",
        stop_reason_code=outcome.stop.reason_code if outcome.stop is not None else None,
        started_at=started_at,
        completed_at=completed_at,
        capability_revision=snapshots.capability.revision,
        capability_snapshot_sha256=snapshots.capability.snapshot_sha256,
        price_snapshot_id=snapshots.prices.snapshot_id,
        price_snapshot_sha256=snapshots.prices.snapshot_sha256,
        tokenizer_revision=snapshots.tokenizer.estimator_revision,
        tokenizer_snapshot_sha256=snapshots.tokenizer.snapshot_sha256,
        profile_revision=profile.revision,
        requested_model=snapshots.capability.requested_model,
        actual_models=tuple(response.actual_model for response in recorded_gateway.responses),
        system_fingerprints=tuple(
            response.system_fingerprint
            for response in recorded_gateway.responses
            if response.system_fingerprint is not None
        ),
        finish_reasons=tuple(response.finish_reason for response in recorded_gateway.responses),
        model_calls=outcome.model_calls,
        tool_calls=outcome.tool_calls,
        provider_attempts=sum(
            len(response.attempts) for response in recorded_gateway.responses
        ),
        retry_attempts=sum(
            len(response.attempts) for response in recorded_gateway.responses
        )
        - len(recorded_gateway.responses),
        charge_ambiguous_attempts=sum(attempt.charge_ambiguous for attempt in attempts),
        released_attempts=ledger.released_attempts,
        prompt_tokens=sum(usage.prompt_tokens for usage in reported),
        cache_hit_tokens=sum(usage.cache_hit_tokens for usage in reported),
        cache_miss_tokens=sum(usage.cache_miss_tokens for usage in reported),
        completion_tokens=sum(usage.completion_tokens for usage in reported),
        reasoning_tokens=sum(usage.reasoning_tokens for usage in reported),
        total_tokens=sum(usage.total_tokens for usage in reported),
        estimated_input_tokens=recording_estimator.estimates,
        estimate_errors=tuple(
            actual - estimated
            for actual, estimated in zip(
                actual_prompt_tokens,
                recording_estimator.estimates[: len(actual_prompt_tokens)],
                strict=True,
            )
        ),
        total_cost_usd=total_cost,
        pessimistic_reserved_usd=ledger.reserved_usd,
        price_bands=tuple(cost.price_band for cost in costs),
        prompt_policy_hash=outcome.prompt_policy_hash,
        rendered_prompt_hash=outcome.rendered_prompt_hash,
        tool_hash=outcome.tool_hash,
        context_hash=outcome.context_hash,
        config_hash=outcome.config_hash,
        sql_sha256=sha256(PROBE_SQL.encode("utf-8")).hexdigest(),
        result_row_count=None,
    )


async def run_probe(
    settings: AppSettings,
    budget: ProbeBudget,
    *,
    gateway: ModelGateway | None = None,
    environment: Mapping[str, str] | None = None,
) -> ProbeEvidence:
    """Run the fixed probe through the complete Retail public composition."""

    if budget != probe_budget():
        raise ProbePreflightError("probe_budget_mismatch")
    snapshots = load_reviewed_snapshots(Path(__file__).parents[1] / "configs" / "model")
    ledger = ProbeLedger(budget=budget, price_snapshot=snapshots.prices)
    if gateway is not None:
        clock = _SystemClock()
        return await _execute_probe(
            snapshots=snapshots,
            budget=budget,
            mode="fake",
            gateway=gateway,
            estimator=_FixedProbeEstimator(),
            query_engine=QueryEngine(
                policy=AstPolicy(),
                executor=_FakeAggregateExecutor(),  # type: ignore[arg-type]
            ),
            knowledge=None,
            resolver=None,
            checkpointer=create_memory_saver(),
            turn_store=InMemoryProviderTurnStore(clock=clock),
            ledger=ledger,
            provider_user_id="0" * 32,
        )

    active_environment = environment if environment is not None else os.environ
    request = fixed_probe_request(snapshots.config_root)
    validate_probe_preflight(active_environment, snapshots, request)
    _validate_real_settings(settings, active_environment)
    ledger.reserve_send(
        estimated_input_tokens=budget.maximum_input_tokens_per_call,
        maximum_output_tokens=budget.maximum_output_tokens_per_call,
    )
    ledger = ProbeLedger(budget=budget, price_snapshot=snapshots.prices)
    estimator = _provisioned_estimator(snapshots)
    assert settings.deepseek_api_key is not None
    assert settings.product_database_dsn is not None
    assert settings.product_knowledge_database_dsn is not None
    assert settings.product_checkpoint_database_dsn is not None
    assert settings.product_model_state_database_dsn is not None
    clock = _SystemClock()
    timeout = httpx.Timeout(connect=10, pool=10, write=30, read=300)
    headers = {"Authorization": f"Bearer {settings.deepseek_api_key.get_secret_value()}"}
    async with httpx.AsyncClient(
        base_url=str(settings.deepseek_base_url),
        headers=headers,
        timeout=timeout,
    ) as client:
        turn_store = PostgresProviderTurnStore(
            settings.product_model_state_database_dsn,
            clock=clock,
        )
        real_gateway = DeepSeekModelGateway(
            client=client,
            turn_store=turn_store,
            capability=snapshots.capability,
            prices=snapshots.prices,
            retry_policy=RetryPolicy(),
            clock=clock,
            sleeper=_sleep,
            jitter_rng=_jitter,
            attempt_guard=ledger,
        )
        query_engine = QueryEngine(
            policy=AstPolicy(),
            executor=PostgresExecutor(settings.product_database_dsn),
        )
        knowledge = KnowledgeModule(
            PostgresKnowledgeStore(settings.product_knowledge_database_dsn)
        )
        resolver = BusinessValueResolver(
            PostgresValueStore(settings.product_database_dsn)
        )
        async with open_postgres_saver(settings.product_checkpoint_database_dsn) as saver:
            return await _execute_probe(
                snapshots=snapshots,
                budget=budget,
                mode="real",
                gateway=real_gateway,
                estimator=estimator,
                query_engine=query_engine,
                knowledge=knowledge,
                resolver=resolver,
                checkpointer=saver,
                turn_store=turn_store,
                ledger=ledger,
                provider_user_id=secrets.token_hex(16),
            )


def write_probe_evidence(evidence: ProbeEvidence, output_path: Path) -> None:
    """Create one redacted JSON evidence artifact without overwriting prior evidence."""

    payload = json.dumps(
        evidence.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/probes/day5-retail-probe.redacted.json"),
    )
    args = parser.parse_args()
    try:
        evidence = asyncio.run(
            run_probe(
                load_settings(os.environ),
                probe_budget(),
                environment=os.environ,
            )
        )
        write_probe_evidence(evidence, args.output)
    except Exception:  # noqa: BLE001 - CLI must suppress tracebacks at the secret boundary.
        print(f"artifact={args.output.resolve()} gate=FAIL")
        return 1
    print(f"artifact={args.output.resolve()} gate={evidence.gate_state}")
    return 0 if evidence.gate_state == "PASS" else 1


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(main())
