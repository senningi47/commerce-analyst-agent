"""Serial deterministic driver over public Product interfaces."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from commerce_agent.context_builder.contracts import ContextDatum
from commerce_agent.model.contracts import RunScope
from commerce_agent.operations.contracts import (
    ActorContext,
    DecisionReceipt,
    DecisionRequest,
    ExecuteRequest,
    ExecutionReceipt,
    ProposalStatus,
)
from commerce_agent.orchestration.contracts import (
    ClarificationRequest,
    InvestigationReport,
    RetailRunOutcome,
    RetailRunRequest,
)
from commerce_agent.product_eval._reset import ScenarioResetPort
from commerce_agent.product_eval.contracts import (
    ProductScenario,
    ProductScenarioResult,
    ScenarioCheck,
)
from commerce_agent.query_engine.contracts import QueryRequest, QueryResult


class RetailGraphPort(Protocol):
    async def run(self, request: RetailRunRequest) -> RetailRunOutcome: ...


class OperationWorkflowPort(Protocol):
    async def decide(self, request: DecisionRequest) -> DecisionReceipt: ...

    async def execute(self, request: ExecuteRequest) -> ExecutionReceipt: ...


class QueryEnginePort(Protocol):
    async def execute(self, request: QueryRequest) -> QueryResult: ...


class ScenarioActorAdapter(Protocol):
    def resolve(self, actor_ref: str) -> ActorContext: ...


class ProductScenarioDriver:
    def __init__(
        self,
        *,
        retail_graph: RetailGraphPort,
        operation_workflow: OperationWorkflowPort,
        query_engine: QueryEnginePort,
        actors: ScenarioActorAdapter,
        reset: ScenarioResetPort,
        config_hash: str = "0" * 64,
    ) -> None:
        self._retail_graph = retail_graph
        self._operation_workflow = operation_workflow
        self._query_engine = query_engine
        self._actors = actors
        self._reset = reset
        self._config_hash = config_hash
        self._serial_lock = asyncio.Lock()

    async def run(self, scenario: ProductScenario) -> ProductScenarioResult:
        async with self._serial_lock:
            try:
                await self._reset.reset(
                    scenario.scenario_id, scenario.reset_manifest_revision
                )
            except Exception:  # noqa: BLE001 - reset adapters have no shared exception base
                return self._failed_result(scenario, "pre_reset_failed")

            try:
                result = await self._run_after_reset(scenario)
            except Exception:  # noqa: BLE001 - cleanup must run for arbitrary port failures
                try:
                    await self._reset.reset(
                        scenario.scenario_id, scenario.reset_manifest_revision
                    )
                finally:
                    raise
            try:
                await self._reset.reset(
                    scenario.scenario_id, scenario.reset_manifest_revision
                )
            except Exception:  # noqa: BLE001 - cleanup failure must become a failed check
                checks = (*result.checks, self._check("cleanup", False, "post_reset_failed"))
                result = result.model_copy(update={"status": "failed", "checks": checks})
            return result

    async def _run_after_reset(self, scenario: ProductScenario) -> ProductScenarioResult:
        asked_slots = []
        invalid_clarifications = 0
        confirmed: list[ContextDatum] = []
        outcome = None
        for attempt_number in range(3):
            request = self._request(scenario, attempt_number, tuple(confirmed))
            outcome = await self._retail_graph.run(request)
            if outcome.status != "needs_input":
                break
            terminal = outcome.terminal
            if not isinstance(terminal, ClarificationRequest):
                break
            scripted = {item.slot: item.response for item in scenario.scripted_clarifications}
            for item in terminal.items:
                asked_slots.append(item.slot)
                response = scripted.get(item.slot)
                if response is None:
                    response = "无法提供更多信息"
                    invalid_clarifications += 1
                confirmed.append(self._datum(item.slot.value, response))
        assert outcome is not None

        proposal_refs = (
            tuple(outcome.terminal.proposal_refs)
            if isinstance(outcome.terminal, InvestigationReport)
            else ()
        )
        receipts: list[ExecutionReceipt] = []
        decisions: list[DecisionReceipt] = []
        for step in scenario.actor_steps:
            if step.proposal_index >= len(proposal_refs):
                continue
            proposal_ref = proposal_refs[step.proposal_index]
            actor = self._actors.resolve(step.actor_ref)
            decision = await self._operation_workflow.decide(
                DecisionRequest(
                    actor=actor,
                    proposal_ref=proposal_ref,
                    decision="approve" if step.action == "approve_execute" else "reject",
                    reason="scenario reviewed evidence",
                )
            )
            decisions.append(decision)
            if step.action == "approve_execute" and decision.grant is not None:
                receipts.append(
                    await self._operation_workflow.execute(
                        ExecuteRequest(actor=actor, grant=decision.grant)
                    )
                )

        readbacks = [
            await self._query_engine.execute(QueryRequest(sql=item.sql))
            for item in scenario.readback_expectations
        ]
        checks = self._validate(
            scenario,
            outcome,
            tuple(asked_slots),
            tuple(receipts),
            tuple(readbacks),
            tuple(decisions),
        )
        report = outcome.terminal if isinstance(outcome.terminal, InvestigationReport) else None
        passed = all(item.passed for item in checks)
        return ProductScenarioResult(
            scenario_id=scenario.scenario_id,
            status="passed" if passed else "failed",
            checks=checks,
            asked_slots=tuple(asked_slots),
            invalid_clarification_count=invalid_clarifications,
            silent_default_count=0,
            proposal_count=len(proposal_refs),
            approval_count=sum(
                item.status == ProposalStatus.APPROVED for item in decisions
            ),
            execution_count=len(receipts),
            audit_event_count=sum(bool(item.audit_ref) for item in receipts),
            readback_count=sum(item.row_count for item in readbacks),
            successful_business_write_count=len(receipts),
            unauthorized_business_write_count=0,
            successful_execution_audit_count=sum(bool(item.audit_ref) for item in receipts),
            sensitivity_comparison_present=bool(
                report and report.sensitivity_comparison is not None
            ),
            claims=tuple(claim.claim_id for claim in report.claims) if report else (),
            unique_proposal_count=len(set(proposal_refs)),
            unique_business_write_count=len(receipts),
            duplicate_audit_count=len(receipts)
            - len({item.audit_ref for item in receipts}),
        )

    def _validate(
        self,
        scenario: ProductScenario,
        outcome: RetailRunOutcome,
        asked_slots,
        receipts,
        readbacks,
        decisions,
    ) -> tuple[ScenarioCheck, ...]:
        report = outcome.terminal if isinstance(outcome.terminal, InvestigationReport) else None
        available_evidence = (
            {item.evidence_id for item in report.available_evidence} if report else set()
        )
        expected_approvals = sum(
            item.action == "approve_execute" for item in scenario.actor_steps
        )
        checks = [
            self._check(
                "terminal_status",
                outcome.status == scenario.expected_terminal_status,
                "terminal_status_mismatch",
            ),
            self._check(
                "required_clarifications",
                set(scenario.required_clarification_slots) <= set(asked_slots),
                "required_clarification_missing",
            ),
            self._check(
                "forbidden_clarifications",
                not set(scenario.forbidden_clarification_slots) & set(asked_slots),
                "forbidden_clarification_asked",
            ),
            self._check(
                "approval_count",
                sum(item.status == ProposalStatus.APPROVED for item in decisions)
                == expected_approvals,
                "approval_count_mismatch",
            ),
            self._check(
                "audit_count",
                sum(bool(item.audit_ref) for item in receipts)
                == sum(item.count for item in scenario.audit_expectations),
                "audit_event_missing",
            ),
            self._check(
                "evidence_ids",
                set(scenario.evidence_ids) <= available_evidence,
                "required_evidence_missing",
            ),
        ]
        for expectation in scenario.operation_expectations:
            if expectation.terminal_status == "succeeded":
                observed_count = sum(
                    item.command_type == expectation.command_type for item in receipts
                )
            else:
                observed_count = sum(
                    item.status == ProposalStatus.REJECTED for item in decisions
                )
            checks.append(
                self._check(
                    f"operation_{expectation.command_type}",
                    observed_count == expectation.count,
                    "operation_count_mismatch",
                )
            )
        for index, (expectation, observed) in enumerate(
            zip(scenario.readback_expectations, readbacks, strict=False)
        ):
            checks.append(
                self._check(
                    f"readback_{index}",
                    tuple(observed.columns) == expectation.expected_columns
                    and observed.row_count >= expectation.minimum_rows,
                    "readback_mismatch",
                )
            )
        return tuple(checks)

    def _request(
        self,
        scenario: ProductScenario,
        attempt_number: int,
        confirmed: tuple[ContextDatum, ...],
    ) -> RetailRunRequest:
        scope = RunScope(
            run_id=uuid5(NAMESPACE_URL, f"scenario:{scenario.scenario_id}"),
            track="retail",
            mode="retail",
            subject_id=scenario.scenario_id,
            experiment_id=scenario.fixture_revision,
            config_hash=self._config_hash,
        )
        return RetailRunRequest(
            run_scope=scope,
            attempt_id=uuid5(
                NAMESPACE_URL, f"scenario:{scenario.scenario_id}:attempt:{attempt_number}"
            ),
            current_input=self._datum("initial_question", scenario.initial_question),
            confirmed_facts=confirmed,
        )

    @staticmethod
    def _datum(source: str, content: str) -> ContextDatum:
        return ContextDatum(
            kind="confirmed_fact" if source != "initial_question" else "user_input",
            namespace="confirmed_fact" if source != "initial_question" else "user_input",
            source_ref=f"scenario:{source}",
            content=content,
            digest=sha256(content.encode()).hexdigest(),
        )

    @staticmethod
    def _check(name: str, passed: bool, reason: str) -> ScenarioCheck:
        return ScenarioCheck(
            name=name,
            passed=passed,
            reason_code="check_passed" if passed else reason,
        )

    def _failed_result(self, scenario: ProductScenario, reason: str) -> ProductScenarioResult:
        return ProductScenarioResult(
            scenario_id=scenario.scenario_id,
            status="failed",
            checks=(self._check("reset", False, reason),),
        )
