import json
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from commerce_agent.operations._approval import ApprovalService, FixedClock, HmacApprovalKeyring
from commerce_agent.operations._backtest import (
    AlertObservation,
    InMemoryAlertObservationPort,
    InMemoryBacktestRegistry,
    MetricAlertBacktester,
    StaticMetricDefinitionPort,
)
from commerce_agent.operations._memory import InMemoryOperationStore
from commerce_agent.operations._seller_refs import (
    InMemorySellerIdentityStore,
    PrivateSellerObservation,
    SellerRefKeyring,
    SellerTargetResolver,
)
from commerce_agent.operations._store import DefaultReferenceValidator
from commerce_agent.operations.commands import (
    CreateAndEnableMetricAlertRule,
    CreateInvestigationFromAlertHit,
    CreateInvestigationTask,
    OpenSellerRiskCase,
)
from commerce_agent.operations.contracts import (
    ActorContext,
    ActorRole,
    AlertBacktestRequest,
    AlertComparator,
    AlertHitRef,
    AlertMetric,
    CalendarWindow,
    EvidenceRef,
    MetricRef,
    ProposeRequest,
    SellerTargetRequest,
)
from commerce_agent.operations.workflow import OperationWorkflow
from commerce_agent.orchestration.contracts import (
    ClarificationItem,
    ClarificationRequest,
    InvestigationClaim,
    InvestigationEvidence,
    InvestigationReport,
    RetailRunOutcome,
    SensitivityComparison,
)
from commerce_agent.product_eval import InMemoryScenarioReset, ProductScenario
from commerce_agent.product_eval.driver import ProductScenarioDriver
from commerce_agent.query_engine.contracts import QueryResult

ROOT = Path(__file__).parents[3]
DEVELOPMENT_FIXTURE = ROOT / "tests" / "fixtures" / "product_eval" / "day4-development.v1.json"
NOW = datetime(2026, 9, 6, 4, 0, tzinfo=UTC)


def load_day4_scenario(scenario_id: str) -> ProductScenario:
    document = json.loads(DEVELOPMENT_FIXTURE.read_text(encoding="utf-8"))
    payload = next(item for item in document["scenarios"] if item["scenario_id"] == scenario_id)
    return ProductScenario.model_validate(payload)


def actor(role: ActorRole, identity: int) -> ActorContext:
    return ActorContext(
        actor_id=UUID(int=identity),
        role=role,
        authentication_ref=f"session:verified:{identity}",
        authenticated_at=NOW,
    )


class Actors:
    def __init__(self) -> None:
        self.analyst = actor(ActorRole.ANALYST, 1)
        self.approver = actor(ActorRole.APPROVER, 2)

    def resolve(self, actor_ref: str) -> ActorContext:
        return {
            "actor:analyst": self.analyst,
            "actor:approver": self.approver,
        }[actor_ref]


class SequenceNonceSource:
    def __init__(self) -> None:
        self._value = 0

    def issue(self) -> bytes:
        self._value += 1
        return self._value.to_bytes(32, "big")


def _observation(
    month: int, *, numerator: int, denominator: int, complete: bool
) -> AlertObservation:
    return AlertObservation(
        window_started_at=datetime(2026, month, 1, tzinfo=UTC),
        window_ended_at=datetime(2026, month + 1, 1, tzinfo=UTC),
        numerator=numerator,
        denominator=denominator,
        complete=complete,
    )


async def operation_fixture(
    scenario: ProductScenario,
) -> tuple[OperationWorkflow, InMemoryOperationStore, tuple[object, ...], Actors]:
    store = InMemoryOperationStore()
    actors = Actors()
    references = DefaultReferenceValidator()
    commands: tuple[object, ...]
    if scenario.scenario_id == "seller-risk-investigation-v1":
        evidence = EvidenceRef(
            kind="query", ref="query:seller_drilldown", digest="a" * 64
        )
        resolver = SellerTargetResolver(
            store=InMemorySellerIdentityStore(
                (
                    PrivateSellerObservation(
                        seller_id="fixture-private-identity",
                        numerator=3,
                        denominator=10,
                        normalized_value=Decimal("0.30"),
                    ),
                )
            ),
            keyring=SellerRefKeyring({1: b"seller-ref-test-key"}, active_version=1),
            clock=FixedClock(NOW),
        )
        candidate = (
            await resolver.find_candidates(
                SellerTargetRequest(
                    metric_ref=MetricRef(name="late_delivery_rate", revision="v1"),
                    observation_started_at=datetime(2026, 8, 1, tzinfo=UTC),
                    observation_ended_at=NOW,
                    anomaly_threshold=Decimal("0.20"),
                    minimum_denominator=5,
                    status_filters=("delivered",),
                    evidence_digest=evidence.digest,
                )
            )
        )[0]
        references = DefaultReferenceValidator(seller_resolver=resolver)
        commands = (
            OpenSellerRiskCase(
                type="open_seller_risk_case",
                seller_ref=candidate.seller_ref,
                observation_started_at=candidate.observation_started_at,
                observation_ended_at=candidate.observation_ended_at,
                metric_ref=MetricRef(name="late_delivery_rate", revision="v1"),
                numerator=candidate.numerator,
                denominator=candidate.denominator,
                observed=candidate.normalized_value,
                threshold=Decimal("0.20"),
                title="Investigate seller delivery risk",
                priority="high",
                evidence_refs=(evidence,),
            ),
        )
    elif scenario.scenario_id == "metric-alert-to-investigation-v1":
        request = AlertBacktestRequest(
            metric=AlertMetric.LATE_DELIVERY_RATE,
            metric_revision="metric.late_delivery_rate.v1",
            grain="global",
            window=CalendarWindow.MONTH,
            comparator=AlertComparator.GREATER_THAN,
            threshold=Decimal("0.20"),
            minimum_denominator=5,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            ended_at=datetime(2026, 4, 1, tzinfo=UTC),
        )
        snapshot = await MetricAlertBacktester(
            metrics=StaticMetricDefinitionPort(),
            observations=InMemoryAlertObservationPort(
                (
                    _observation(1, numerator=1, denominator=2, complete=True),
                    _observation(2, numerator=8, denominator=10, complete=False),
                    _observation(3, numerator=3, denominator=10, complete=True),
                )
            ),
        ).run(request)
        references = DefaultReferenceValidator(
            backtests=InMemoryBacktestRegistry((snapshot,))
        )
        backtest_evidence = EvidenceRef(
            kind="alert_backtest",
            ref=f"backtest:{snapshot.backtest_ref.backtest_id}",
            digest=snapshot.backtest_ref.rule_spec_sha256,
        )
        hit_evidence = EvidenceRef(
            kind="alert_hit", ref="alert-hit:fixture", digest="b" * 64
        )
        commands = (
            CreateAndEnableMetricAlertRule(
                type="create_and_enable_metric_alert_rule",
                alert_backtest_ref=snapshot.backtest_ref,
                metric_ref=MetricRef(
                    name=request.metric.value, revision=request.metric_revision
                ),
                grain=request.grain,
                window=request.window.value,
                comparator=request.comparator.value,
                threshold=request.threshold,
                minimum_denominator=request.minimum_denominator,
                filter_refs=request.filter_refs,
                evidence_refs=(backtest_evidence,),
            ),
            CreateInvestigationFromAlertHit(
                type="create_investigation_from_alert_hit",
                alert_hit_ref=AlertHitRef(
                    hit_id=UUID(int=900), evidence_digest=hit_evidence.digest
                ),
                title="Investigate reviewed late-delivery alert",
                priority="high",
                evidence_refs=(hit_evidence,),
            ),
        )
    else:
        evidence = EvidenceRef(kind="query", ref="query:gmv_drilldown", digest="c" * 64)
        commands = (
            CreateInvestigationTask(
                type="create_investigation_task",
                title="Investigate GMV movement",
                priority="high",
                public_summary="Validate the evidence-backed GMV contributors.",
                evidence_refs=(evidence,),
            ),
        )
    workflow = OperationWorkflow(
        store=store,
        references=references,
        clock=FixedClock(NOW),
        approvals=ApprovalService(
            clock=FixedClock(NOW),
            nonce_source=SequenceNonceSource(),
            keyring=HmacApprovalKeyring({1: b"operation-test-key"}, active_version=1),
        ),
    )
    return workflow, store, commands, actors


class FixtureRetailGraph:
    def __init__(
        self,
        scenario: ProductScenario,
        workflow: OperationWorkflow,
        commands: tuple[object, ...],
        actors: Actors,
    ) -> None:
        self._scenario = scenario
        self._workflow = workflow
        self._commands = commands
        self._actors = actors

    async def run(self, request) -> RetailRunOutcome:
        if self._scenario.required_clarification_slots and not request.confirmed_facts:
            clarification = ClarificationRequest(
                conversation_id=request.run_scope.subject_id,
                request_id=uuid5(NAMESPACE_URL, f"clarify:{self._scenario.scenario_id}"),
                items=tuple(
                    ClarificationItem(slot=slot, question=f"Provide {slot.value}.")
                    for slot in self._scenario.required_clarification_slots
                ),
            )
            return self._outcome(request, "needs_input", clarification)

        proposals = []
        for index, command in enumerate(self._commands):
            proposals.append(
                await self._workflow.propose(
                    ProposeRequest(
                        actor=self._actors.analyst,
                        command=command,
                        evidence_refs=command.evidence_refs,
                        idempotency_key=f"{self._scenario.scenario_id}:{index}",
                    )
                )
            )
        evidence = tuple(
            InvestigationEvidence(
                evidence_id=evidence_id,
                kind=(
                    "reconciliation"
                    if evidence_id.startswith("reconciliation:")
                    else "query"
                ),
                summary=f"Deterministic evidence {evidence_id}.",
                digest=sha256(evidence_id.encode()).hexdigest(),
            )
            for evidence_id in self._scenario.evidence_ids
        )
        claim_ids = (
            ("historical_window_hits", "historical_window_coverage")
            if self._scenario.scenario_id == "metric-alert-to-investigation-v1"
            else ("evidence_backed_result",)
        )
        query_evidence = tuple(
            item.evidence_id for item in evidence if item.kind == "query"
        )
        report = InvestigationReport(
            conversation_id=request.run_scope.subject_id,
            report_id=uuid5(NAMESPACE_URL, f"report:{self._scenario.scenario_id}"),
            claims=tuple(
                InvestigationClaim(
                    claim_id=claim_id,
                    text=f"Deterministic claim {claim_id}.",
                    evidence_refs=query_evidence,
                )
                for claim_id in claim_ids
            ),
            available_evidence=evidence,
            reconciliation_refs=tuple(
                item.evidence_id for item in evidence if item.kind == "reconciliation"
            ),
            proposal_refs=tuple(item.proposal_ref for item in proposals),
            selected_risk_status=(
                "confirmed"
                if self._scenario.scenario_id == "seller-risk-investigation-v1"
                else None
            ),
            sensitivity_comparison=(
                SensitivityComparison(
                    all_sellers_evidence_ref="query:baseline",
                    selected_risk_evidence_ref="query:seller_drilldown",
                    summary="Compared all sellers with the selected risk cohort.",
                )
                if self._scenario.scenario_id == "seller-risk-investigation-v1"
                else None
            ),
        )
        return self._outcome(request, "completed", report)

    @staticmethod
    def _outcome(request, status: str, terminal) -> RetailRunOutcome:
        return RetailRunOutcome(
            status=status,
            terminal=terminal,
            model_calls=1,
            tool_calls=1,
            prompt_policy_hash="1" * 64,
            rendered_prompt_hash="2" * 64,
            tool_hash="3" * 64,
            context_hash="4" * 64,
            config_hash=request.run_scope.config_hash,
            attempt_id=request.attempt_id,
        )


class StoreReadbackPort:
    def __init__(self, store: InMemoryOperationStore) -> None:
        self._store = store

    async def execute(self, request) -> QueryResult:
        sql = request.sql.casefold()
        if "risk_annotations" in sql:
            return QueryResult(
                columns=["seller_ref", "status"],
                rows=[
                    {"seller_ref": f"seller-ref:{item.risk_id}", "status": item.status}
                    for item in self._store.risks
                ],
            )
        if "enabled_metric_alert_rules" in sql:
            return QueryResult(
                columns=["rule_ref", "status"],
                rows=[
                    {"rule_ref": f"rule:{item.rule_id}", "status": item.status}
                    for item in self._store.rules
                ],
            )
        if "enabled_metric_alert_hits" in sql:
            return QueryResult(
                columns=["hit_ref", "rule_ref"],
                rows=[
                    {"hit_ref": "alert-hit:fixture", "rule_ref": f"rule:{item.rule_id}"}
                    for item in self._store.rules
                ],
            )
        if "task_ref" in sql:
            return QueryResult(
                columns=["task_ref", "status"],
                rows=[
                    {"task_ref": f"task:{item.task_id}", "status": item.status.value}
                    for item in self._store.tasks
                ],
            )
        return QueryResult(
            columns=["status"], rows=[{"status": item.status.value} for item in self._store.tasks]
        )


async def in_memory_driver(
    scenario: ProductScenario,
) -> tuple[ProductScenarioDriver, InMemoryOperationStore]:
    workflow, store, commands, actors = await operation_fixture(scenario)
    return (
        ProductScenarioDriver(
            retail_graph=FixtureRetailGraph(scenario, workflow, commands, actors),
            operation_workflow=workflow,
            query_engine=StoreReadbackPort(store),
            actors=actors,
            reset=InMemoryScenarioReset(),
        ),
        store,
    )


@pytest.mark.asyncio
async def test_seller_risk_scenario_completes_approval_execution_readback() -> None:
    scenario = load_day4_scenario("seller-risk-investigation-v1")
    driver, store = await in_memory_driver(scenario)

    result = await driver.run(scenario)

    assert result.status == "passed"
    assert result.unauthorized_business_write_count == 0
    assert result.execution_count == 1
    assert result.audit_event_count == 1
    assert result.readback_count >= 2
    assert result.sensitivity_comparison_present is True
    assert len(store.tasks) == 1
    assert len(store.risks) == 1
    assert store.risks[0].status == "under_investigation"


@pytest.mark.asyncio
async def test_alert_hit_to_task_requires_second_approval() -> None:
    scenario = load_day4_scenario("metric-alert-to-investigation-v1")
    driver, store = await in_memory_driver(scenario)

    result = await driver.run(scenario)

    assert result.status == "passed"
    assert result.proposal_count == 2
    assert result.approval_count == 2
    assert result.execution_count == 2
    assert result.claims == ("historical_window_hits", "historical_window_coverage")
    assert len(store.rules) == 1
    assert len(store.tasks) == 1


@pytest.mark.asyncio
async def test_ambiguous_gmv_scenario_never_infers_required_values() -> None:
    scenario = load_day4_scenario("ambiguous-gmv-investigation-v1")
    driver, _store = await in_memory_driver(scenario)

    result = await driver.run(scenario)

    assert result.status == "passed"
    assert set(result.asked_slots) >= {
        "gmv_metric_definition",
        "valid_order_statuses",
        "time_field",
        "time_range",
        "analysis_grain",
        "minimum_order_count",
        "anomaly_threshold",
    }
    assert result.silent_default_count == 0
