# Day 4 Product Closed Loops Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the three approved Product investigation/operations loops with typed clarification, constrained SQL reasoning, independent approval, atomic audited execution, readback, and deterministic Product scenario validation.

**Architecture:** Keep one modular Product application and one separate BIRD trust domain. `RetailGraph` owns analysis orchestration and may only create a proposal through `OperationWorkflow.propose()`; trusted callers alone hold `decide()` and `execute()`. Product persistence stays behind in-memory and PostgreSQL adapters, while `ProductScenarioDriver` depends only on public Product interfaces and a separately held reset capability.

**Tech Stack:** Python 3.11, Pydantic v2, LangGraph 1.2.x, psycopg 3.3.x, PostgreSQL 18, SQLAlchemy 2/Alembic, SQLGlot 30.x, standard-library JSON/hash/HMAC/Decimal/secrets, pytest/pytest-asyncio, Ruff.

**Spec:** Global authority: `docs/project/specs/BIRD-Interact电商经营分析Agent-设计规格-v0.3.md` (SHA-256 `33466c117bc35ac036c334bf2b120bba4071605545d19e09d4f086df78290c81`). Approved Day 4 refinement: `docs/project/specs/2026-09-06-day-4-product-closed-loops-design.md` (SHA-256 `d76d260e1dddd070f4f1d89d4d6145db6e9457b71a6a271fe13c5a1cbf842386`). If they conflict, stop and bring the conflict to review; the global v0.3 specification wins.

## Global Constraints

- The global v0.3 specification is authoritative. This plan may refine its interfaces and order, but may not replace its Product/BIRD isolation, SQL safety, approval, audit, evaluation, budget, or evidence rules.
- Preserve the modular Product application and independent BIRD official trust domain. Product code, state, stores, credentials, operations, Knowledge, Resolver, Product QueryEngine, and scenario capabilities must not enter BirdA/BirdC constructors, profiles, tools, fixtures, or runtime state.
- `OperationWorkflow` has exactly three public behavior methods: `propose(ProposeRequest) -> ProposalSnapshot`, `decide(DecisionRequest) -> DecisionReceipt`, and `execute(ExecuteRequest) -> ExecutionReceipt`.
- RetailGraph may call only `OperationWorkflow.propose()`. It must never expose, register, import as a model tool, or persist an `ExecutionGrant`, approval signer, approval credential, `decide`, or `execute` capability.
- Every proposal contains exactly one discriminated `OperationCommand`. An inseparable two-object mutation uses the named `OpenSellerRiskCase`; there is no generic command list, arbitrary patch, caller-selected handler, URL, or write SQL.
- `ProposalVersion` is immutable. Payload or target-version changes create a new version and require new approval. Only the latest pending version may be revised; terminal versions start a new proposal lineage.
- Approval is first-decision-wins; requester and approver must differ. A proposal expires after 24 hours. An approved grant expires after 10 minutes and can succeed once.
- Canonical JSON is UTF-8, sorted-key, compact, finite, and schema-versioned. Decimal values use normalized strings, UUIDs use lowercase canonical strings, and UTC timestamps use fixed RFC 3339 `Z` form. HMAC-SHA256 binds command hash, proposal/version, requester, approver, target versions, expiry, nonce, and key version.
- The database stores only a nonce digest. Nonce claim, business mutation, successful execution state, and append-only audit occur in one transaction. Invalid preflight and rolled-back transactions do not consume a valid nonce.
- Execute retries use one execution identity and one idempotency binding. A known committed retry returns the original receipt. Ambiguous delivery yields `OperationOutcomeUnknown` and readback by execution identity; it never generates a new grant or repeats a write blindly.
- Application roles are non-inheriting and deny-by-default: Analyst, Approver, and Data Admin are distinct domain roles; proposal writer, approval writer, operation executor, trace writer, agent reader, owners, and reset role are distinct database identities.
- `operation_executor` has no direct business-table or audit-table DML. It can only execute the exact reviewed typed functions and read its execution receipt. All application roles lack audit UPDATE/DELETE/TRUNCATE.
- Day 4 does not use RLS. Use schema/table/view/function ACLs, independent NOLOGIN owners, fixed `SECURITY DEFINER` functions, qualified names, fixed `trusted_schema, pg_temp` search paths, no dynamic SQL, revoked `PUBLIC EXECUTE`, and targeted grants.
- `ops_read` is the only analysis readback route. Public contracts, model context, checkpoint, Trace, audit, scenario results, reports, and `ops_read` never contain a raw seller ID, nonce, signature, HMAC key, session data, DSN, complete provider payload, or private reasoning.
- `SellerRef` is opaque, versioned, evidence-bound, namespaced, expiring, and tamper-evident. It cannot be decoded to a seller ID and cannot be produced by trigram or vector matching.
- Metric backtesting uses fixed parameterized SQL for versioned late-delivery, low-rating, and cancellation metrics. It reports only deterministic historical-window hits, exclusions, and coverage; it does not claim prediction accuracy, future impact, profit, or causal ROI.
- GMV remains `clarification_required`. The system cannot infer GMV, valid-order status, time field/range, analysis grain, minimum order count, or anomaly threshold when the scenario requires them. No risk status silently removes sellers from GMV.
- Global Product limits remain at most 2 valid clarification rounds, 2 replans, 3 SQL repairs, and 12 tool calls. Changes require new profile/config hashes and cannot reinterpret old checkpoints.
- A model cannot complete Retail with ordinary prose. Success requires the typed `InvestigationReport` terminal action after all required evidence and reconciliation checks. Missing user input produces `needs_input`; all other stops use the six global stop primitives.
- Default pytest and CI remain offline: they do not connect to PostgreSQL, change database state, call a paid provider, or possess reset credentials. PostgreSQL tests require `COMMERCE_AGENT_RUN_POSTGRES_TESTS=1`, explicit invocation, and `--tb=line`.
- Do not open, print, search, or commit `.env`. An explicitly authorized command may consume it through `uv run --env-file .env`. Never access, enumerate, search, index, or count evaluator-only/evaluator_only content.
- Product scenario development and regression fixtures may be read by implementers. No final-closed reference SQL, scripted answers, assertions, reset credential, or hidden evaluation state may enter Product prompts, Knowledge, ordinary Trace, or development fixtures.
- The current repository is `main`, unborn, with 0 commits and mostly untracked user files. Preserve all user content. An empty `git diff` is not evidence that the working tree is empty.
- Do not execute `git add`, `git commit`, `git push`, `git clean`, or `git reset` while implementing ordinary tasks. Git checkpoint commands exist only in the final independent Git Gate and need separate authorization.
- TDD is mandatory per vertical slice: add one named test, run that exact node and observe the specified failure, add the smallest implementation, rerun green, refactor while green, then continue. Broad suites are regression checks, not substitutes for red/green/refactor.
- The user's learning boundary remains active: the user must personally implement and explain approval/transaction/audit, `SqlReasoner`, `ProductScenarioDriver`, BIRD isolation, and critical tests. Every core task review records what it is, why it exists, its failure case, invariants, dependencies, alternatives, portability, and verification.

## Authorization Gates

| Gate | Deliverable | May be prepared by preceding work? | Requires a separate authorization before execution? | Passing evidence |
|---|---|---:|---:|---|
| Plan Gate | This document only | Yes, current authorization | Plan only | User review of this file |
| Implementation Gate | Tasks 1-12 offline Product source/tests | No code is authorized by plan approval alone | Yes | User explicitly starts implementation |
| Migration Source Gate | Task 13 creates `0004_day4_product_operations.py` and static tests | Only after implementation begins and reviewer reaches this gate | Yes, distinct source-review gate | Static migration contract green; migration not applied |
| Role Provisioning Source Gate | Task 14 creates credential/provisioning source and unit tests | Only after Migration Source Gate review | Yes, distinct source-review gate | Unit tests green; no credential or role changed |
| Database Activation Gate | Task 16 provisions roles/credentials and applies migration | Source may exist; state change may not occur | Yes, explicit database mutation authorization | Exact preflight, provision, upgrade, ACL tests, session count |
| Real PostgreSQL Write Gate | Task 17 runs the seller-risk write/readback scenario | PostgreSQL adapter/tests may exist; write may not occur | Yes, explicit real Product database write authorization | Negative writes 0; one positive transaction/readback; cleanup verified |
| Paid Provider Gate | Optional Gate P; Day 4 does not require it | FakeModel coverage is sufficient | Yes, with new identity, artifact, and cost ceiling | Normally `NOT REQUIRED / NOT RUN` for Day 4 |
| Git Checkpoint Gate | Gate G stages an explicit allowlist and creates a checkpoint | All implementation and reports may exist | Yes, explicit Git authorization | Explicit path review and resulting commit ID |

Approval of an earlier gate never authorizes a later row. Writing migration or provisioning source never authorizes executing it. Database Activation does not authorize a Product write scenario. Day 4 completion does not authorize a paid call or Git operation.

## File Map

| Path | Responsibility |
|---|---|
| `src/commerce_agent/operations/contracts.py` | Actor, reference, proposal, decision, grant, execution, business-state, preview, and receipt contracts |
| `src/commerce_agent/operations/commands.py` | Closed eight-command discriminated union and command-specific fields/invariants |
| `src/commerce_agent/operations/errors.py` | Stable sanitized operation error taxonomy with `reason_code` and `retryable` |
| `src/commerce_agent/operations/_canonical.py` | Canonical projections, Decimal/UUID/UTC normalization, payload and target-version digests |
| `src/commerce_agent/operations/_approval.py` | Injected clock/nonce/keyring, grant issue/verification, constant-time HMAC comparison |
| `src/commerce_agent/operations/_store.py` | Internal high-level store protocol and persistence DTOs; no public adapter leakage |
| `src/commerce_agent/operations/_memory.py` | Lock-protected deterministic store for state-machine and scenario tests |
| `src/commerce_agent/operations/_seller_refs.py` | Opaque seller-target issue/resolve capability and private identity-store protocol |
| `src/commerce_agent/operations/_backtest.py` | Fixed metric definitions, calendar-window replay, snapshot/hash binding |
| `src/commerce_agent/operations/_postgres.py` | Role-bound Product PostgreSQL operation, seller-target, backtest, and readback adapters |
| `src/commerce_agent/operations/workflow.py` | The only implementation of `propose`, `decide`, and `execute` orchestration |
| `src/commerce_agent/operations/__init__.py` | Deliberate public exports only; no signer/store/private target exports |
| `src/commerce_agent/sql_reasoning/contracts.py` | `SqlReasoningRequest`, `ProductDbError`, fingerprints, candidate, and attempt summary |
| `src/commerce_agent/sql_reasoning/errors.py` | Stable reasoner contract/config/infrastructure errors |
| `src/commerce_agent/sql_reasoning/reasoner.py` | Generate/repair loop over ContextBuilder + ModelGateway; no SQL execution |
| `src/commerce_agent/sql_reasoning/__init__.py` | Public reasoner contracts and `SqlReasoner` export |
| `src/commerce_agent/trace/contracts.py` | Provider-neutral `ScopedTraceEvent`, event types, `TracePort`, and redaction contract |
| `src/commerce_agent/trace/errors.py` | Trace contract and infrastructure errors |
| `src/commerce_agent/trace/module.py` | Sequence validation, redaction, and append-only `TraceModule.append()` |
| `src/commerce_agent/trace/_memory.py` | Attempt-scoped deterministic trace store |
| `src/commerce_agent/trace/_postgres.py` | Product-only PostgreSQL trace adapter using only `trace_writer` |
| `src/commerce_agent/query_engine/contracts.py` | Reconciliation rules, requests, check results, and evidence refs in addition to query contracts |
| `src/commerce_agent/query_engine/engine.py` | Existing `execute()` plus deep `reconcile()` behavior |
| `src/commerce_agent/query_engine/_ast_policy.py` | Reviewed `ops_read` view/column allowlist without raw `ops` access |
| `src/commerce_agent/orchestration/contracts.py` | Typed clarification, plan, report, Day 4 Retail request/outcome, and terminal union |
| `src/commerce_agent/orchestration/_checkpoint.py` | Day 4 state/node revisions and JSON-like persisted analysis/proposal state |
| `src/commerce_agent/orchestration/tools.py` | Closed Product model actions and internal module dispatch; no approve/execute/grant |
| `src/commerce_agent/orchestration/retail_graph.py` | Clarify/plan/reason/query/reconcile/propose/report graph orchestration only |
| `src/commerce_agent/context_builder/contracts.py` | Day 4 Product namespaces and SQL/plan/report prompt steps |
| `src/commerce_agent/context_builder/profiles.py` | Versioned Day 4 Retail config loading; unchanged BirdA/BirdC semantics |
| `configs/model/run-profiles.v2.json` | Retail v2 tools/namespaces/limits and byte-identical logical BIRD v1 policies |
| `configs/model/prompt-policies.v2.json` | Retail v2 clarification/plan/SQL/report rules; common/BIRD policies unchanged |
| `src/commerce_agent/product_eval/contracts.py` | Immutable scenario, scripted actor step, assertions, and result contracts |
| `src/commerce_agent/product_eval/driver.py` | Serial deterministic scenario runner through public Product interfaces |
| `src/commerce_agent/product_eval/_reset.py` | Reset port and PostgreSQL adapter; capability never exported to Product code |
| `tests/support/product_eval_failures.py` | Test-only adapter decorators for the four approved failure boundaries |
| `tests/fixtures/product_eval/day4-development.v1.json` | One deterministic fixture for each of the three Product loops |
| `tests/fixtures/product_eval/day4-regression.v1.json` | Negative/recovery fixtures without final-closed answers |
| `db/migrations/versions/0004_day4_product_operations.py` | Ops/app objects, views, constraints, safe functions, ownership, and object ACLs |
| `scripts/provision_day4_operations_env.py` | Atomic creation of missing ignored Day 4 secrets/DSNs without printing them |
| `scripts/bootstrap_day4_operations.py` | Idempotent role provisioning and exact role hardening; no migration call |
| `.env.example` | Empty Day 4 credential/DSN/key names only |
| `src/commerce_agent/config.py` | Optional typed Day 4 `SecretStr` settings; no process-state read at import |
| `tests/unit/operations/` | Commands, canonicalization, approval, state, workflow, seller refs, backtest, memory adapter |
| `tests/unit/sql_reasoning/` | Generation, repair, fingerprint, budget, and no-progress tests |
| `tests/unit/trace/` | Event binding, sequence, redaction, memory store, and failure semantics |
| `tests/unit/query_engine/` | Reconciliation plus `ops_read` AST allowlist tests |
| `tests/unit/orchestration/` | Day 4 contracts, dispatcher, Retail graph, checkpoint, profile, privacy, BIRD isolation |
| `tests/unit/product_eval/` | Scenario validation, scripted clarification, driver assertions, and failure decorators |
| `tests/unit/scripts/` | Migration/provision source and preflight behavior without database mutation |
| `tests/integration/operations/` | PostgreSQL workflow, nonce, rollback, audit, idempotency, and ACL tests |
| `tests/integration/product_eval/` | PostgreSQL reset-capability and real seller-risk scenario tests |
| `tests/integration/orchestration/` | PostgreSQL readback and checkpoint/resume equivalence tests |
| `tests/integration/trace/` | Trace append ACL, redaction, sequence, and session-close tests |
| `tests/integration/conftest.py` | Day 4 application names added to the zero-session-leak assertion |
| `docs/reports/2026-09-06-day-4-product-closed-loops.md` | Commands, counts, ACL matrix, scenario evidence, omissions, and Gate decision |

## Dependency Order

```text
Task 1 domain contracts/errors
  -> Task 2 canonical approval primitives
  -> Task 3 Trace contracts/memory
  -> Task 4 in-memory store + OperationWorkflow
       -> Task 5 SellerRef
       -> Task 6 MetricAlertBacktester
       -> Task 7 SqlReasoner
       -> Task 8 QueryEngine reconciliation/readback policy
            -> Task 9 Retail contracts/profile/tools
            -> Task 10 RetailGraph/checkpoint integration
                 -> Task 11 ProductScenarioDriver
                 -> Task 12 three deterministic loops + recovery
                      -> Gate M: Task 13 migration source
                      -> Gate R: Task 14 role-provisioning source
                      -> Task 15 PostgreSQL adapters/integration contracts
                      -> Gate D: Task 16 actual role/migration activation
                      -> Gate W: Task 17 real PostgreSQL seller-risk write loop
                      -> Task 18 final non-paid verification/report
                      -> Gate P: paid provider remains closed unless separately authorized
                      -> Gate G: Git checkpoint remains closed unless separately authorized
```

Tasks 5-8 may be reviewed independently after Task 4, but Task 9 starts only after all four are green. No PostgreSQL source task may be used to infer permission to execute a database command.

## Test Fixture Helper Contract

Code snippets below use deterministic same-file builders to keep each assertion readable. These builders are test support, not production APIs; create them in the owning test file before the first test that calls them. Their exact contracts are:

| Task | Helper names | Deterministic return/binding |
|---|---|---|
| 1 | `valid_create_task`, `valid_transition_payload`, `valid_decision_receipt` | Frozen Task 1 DTOs using UUID(int=...), UTC 2026-09-06 times, finite Decimals, and `a`-prefixed hashes |
| 2 | `valid_envelope_payload`, `approved_fixture` | Golden-vector envelope mapping; `(ApprovalService, StoredApproval, ExecutionGrant)` with fixed clock/nonce/key v1 |
| 3 | `scoped_event`, `TEST_SCOPE`, `TEST_ATTEMPT` | One fixed Retail RunScope/attempt and a fully valid `ScopedTraceEvent` copied only through typed updates |
| 4 | `actor`, `workflow_fixture`, `valid_propose_request`, `decision_request`, `approved_workflow_fixture`, `execute_transition_fixture` | Fixed ActorContext factory; workflow with memory store/clock/signer/Trace; approved fixture returns `(workflow, store, grant)` |
| 5 | `seller_resolver_fixture`, `private_seller_observation`, `seller_target_request`, `mutated_seller_ref_fixture`, `issued_seller_ref`, `seller_risk_request` | Private identity fixtures stay in test support; public returns contain only aggregate evidence and SellerRef |
| 6 | `observation`, `backtester_fixture`, `backtest_request`, `alert_workflow_fixture`, `create_alert_rule_command` | Calendar-aligned UTC windows, fixed metric revision, typed Decimal values, and bound snapshot |
| 7 | `sql_candidate_response`, `sql_reasoner_fixture`, `sql_reasoning_request`, `sql_candidate` | FakeModel response plus fixed ContextBuilder/profile/scope; no database/provider connection |
| 8 | `in_memory_query_engine`, `evidence_result`, `reconciliation_fixture` | Recording executor plus typed evidence for the named total/grain/set/NULL/Decimal case |
| 9 | `retail_outcome`, `investigation_plan`, `investigation_report`, `claim`, `evidence`, `dispatcher_fixture`, `tool_call` | Strict Day 4 terminal/control DTOs and a proposal-recording public workflow port |
| 10 | `retail_graph_fixture`, `clarification_call`, `retail_request`, `answers_for`, `resumable_graph_fixture`, `uninterrupted_graph_fixture`, `complete_investigation_request`, `graph_with_one_cleanup_failure` | Fully composed FakeModel/in-memory Retail graph; each helper exposes counters named in assertions |
| 11 | `load_fixture`, `seller_risk_scenario`, `observed_result`, `validate_scenario`, `production_python_sources`, `imported_modules` | Loads only the two named Product development/regression fixture files; AST helpers receive explicit Product source paths |
| 12 | `in_memory_driver`, `load_day4_scenario`, `load_day4_regression`, `recovery_driver`, `recovery_scenario` | Serial Driver with FakeModel/memory/reset spy and exact scenario ID lookup from named visible fixtures |
| 13 | `load_migration`, `RecordingOperations`, `recorded_sql` | Imports only revision 0004 and records `op.execute`/table/index operations without a connection |
| 14 | `parse_day4_values`, `FakeCursor`, `fake_connect`, `distinct_passwords` | Parses only the temporary test content; fake cursor records SQL; passwords are unique fixed test strings |
| 15 | `NeverConnect`, `postgres_adapter_fixture`, `approved_execute_request`, `business_write_count`, `execution_audit_count`, `consumed_nonce_count` | Unit helper rejects before I/O; integration helpers use exact test roles and scenario-scoped count queries |

Each helper must itself have a focused assertion or be simple enough to inspect inline. A helper cannot bypass the public method under test, mutate an internal store to manufacture the expected result, read hidden evaluation content, or hide a model/database/network call.

---

## Phase A: Offline Product Core

### Task 1: Freeze Operation Commands, Lifecycle Contracts, and Sanitized Errors

**Files:**
- Create: `src/commerce_agent/operations/__init__.py`
- Create: `src/commerce_agent/operations/contracts.py`
- Create: `src/commerce_agent/operations/commands.py`
- Create: `src/commerce_agent/operations/errors.py`
- Create: `tests/unit/operations/__init__.py`
- Create: `tests/unit/operations/test_contracts.py`
- Create: `tests/unit/operations/test_commands.py`
- Create: `tests/unit/operations/test_errors.py`

**Interfaces:**
- Produces: `ActorRole`, `ActorContext`, `EvidenceRef`, `ProposalRef`, `ProposalStatus`, `ExecutionStatus`, `InvestigationStatus`, `RiskDisposition`, `MetricRef`, `SellerRef`, `InvestigationTaskRef`, `AlertBacktestRef`, `AlertHitRef`, `CommandPreview`, `ProposeRequest`, `ProposalSnapshot`, `DecisionRequest`, `DecisionReceipt`, `ExecutionGrant`, `ExecuteRequest`, and `ExecutionReceipt`.
- Produces: `OpenSellerRiskCase`, `CreateInvestigationTask`, `CreateInvestigationFromAlertHit`, `CreateAndEnableMetricAlertRule`, `AssignInvestigation`, `TransitionInvestigation`, `AddInvestigationConclusion`, `CloseInvestigation`, and discriminated `OperationCommand`.
- Produces: `OperationContractError`, `OperationAuthorizationError`, `ApprovalError`, `OperationConflictError`, `OperationInfrastructureError`, and `OperationOutcomeUnknown`; each exposes a stable lowercase `reason_code` and `retryable`, and no error string exposes an input payload.
- Consumes: only Pydantic/stdlib provider-neutral types. This task cannot import orchestration, QueryEngine, database adapters, or BIRD.

**Closed command field matrix:**

| Command | Required typed fields | Target/version rule | Evidence rule |
|---|---|---|---|
| `OpenSellerRiskCase` | `seller_ref`, UTC observation range, `metric_ref`, numerator/denominator, observed `Decimal`, threshold `Decimal`, title, priority | New risk + task, expected version `0` | validated seller/query evidence |
| `CreateInvestigationTask` | title, priority, public summary | New task, expected version `0` | at least one Product evidence ref |
| `CreateInvestigationFromAlertHit` | `AlertHitRef`, title, priority | New task, expected version `0` | enabled-rule hit evidence |
| `CreateAndEnableMetricAlertRule` | `AlertBacktestRef`, metric revision, grain, calendar window, comparator, threshold, minimum denominator, filter refs | New enabled rule, expected version `0` | exact backtest spec hash |
| `AssignInvestigation` | `InvestigationTaskRef`, assignee display ref | Exact task version | existing task evidence |
| `TransitionInvestigation` | `InvestigationTaskRef`, exact from/to state, reason code | Exact task version | allowed state edge |
| `AddInvestigationConclusion` | `InvestigationTaskRef`, conclusion code/summary/evidence | Exact task version | non-empty evidence refs |
| `CloseInvestigation` | `InvestigationTaskRef`, conclusion ref, risk disposition when linked | Exact task version | resolved task + conclusion |

- [ ] **Step 1: Write the failing actor, immutability, and single-command tests**

```python
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from commerce_agent.operations.commands import CreateInvestigationTask
from commerce_agent.operations.contracts import ActorContext, ActorRole, EvidenceRef, ProposeRequest


def test_propose_request_has_one_trusted_actor_and_one_frozen_command() -> None:
    actor = ActorContext(
        actor_id=UUID(int=1),
        role=ActorRole.ANALYST,
        authentication_ref="session:verified:1",
        authenticated_at=datetime(2026, 9, 6, 4, 0, tzinfo=UTC),
    )
    evidence = EvidenceRef(
        kind="query",
        ref="query:evidence-1",
        digest="a" * 64,
    )
    command = CreateInvestigationTask(
        type="create_investigation_task",
        title="Review delayed deliveries",
        priority="high",
        public_summary="Validate the affected cohort and document findings.",
        evidence_refs=(evidence,),
        expected_target_version=0,
    )
    request = ProposeRequest(
        actor=actor,
        command=command,
        evidence_refs=(evidence,),
        idempotency_key="day4-scenario-1-proposal-1",
    )

    assert request.command.type == "create_investigation_task"
    with pytest.raises(ValidationError):
        request.command.title = "changed"
```

- [ ] **Step 2: Run the test and observe the contract red state**

Run: `uv run pytest tests/unit/operations/test_contracts.py::test_propose_request_has_one_trusted_actor_and_one_frozen_command -q`

Expected: FAIL during collection because `commerce_agent.operations` does not exist.

- [ ] **Step 3: Implement the shared base contracts and the eight-command discriminator**

```python
class ActorContext(BaseModel, frozen=True, extra="forbid"):
    actor_id: UUID
    role: ActorRole
    authentication_ref: str = Field(min_length=1, max_length=256)
    authenticated_at: datetime


class CreateInvestigationTask(BaseModel, frozen=True, extra="forbid"):
    type: Literal["create_investigation_task"]
    title: str = Field(min_length=1, max_length=200)
    priority: Literal["low", "medium", "high"]
    public_summary: str = Field(min_length=1, max_length=2_000)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=32)
    expected_target_version: Literal[0] = 0


OperationCommand = Annotated[
    OpenSellerRiskCase
    | CreateInvestigationTask
    | CreateInvestigationFromAlertHit
    | CreateAndEnableMetricAlertRule
    | AssignInvestigation
    | TransitionInvestigation
    | AddInvestigationConclusion
    | CloseInvestigation,
    Field(discriminator="type"),
]
```

Every model uses `frozen=True, extra="forbid"`; tuple fields replace mutable lists. Validate timezone awareness, range ordering, finite/bounded Decimal scale, unique evidence refs, state edges, and command-specific target rules inside the owning model.

- [ ] **Step 4: Add one red/green slice for every rejected command shape and state edge**

Add and execute one at a time:

```python
@pytest.mark.parametrize(
    "payload_update",
    [
        {"unknown": "field"},
        {"expected_target_version": 1},
        {"evidence_refs": ()},
    ],
)
def test_create_task_fails_closed(payload_update: dict[str, object]) -> None:
    payload = valid_create_task().model_dump(mode="python") | payload_update
    with pytest.raises(ValidationError):
        CreateInvestigationTask.model_validate(payload)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("open", "closed"),
        ("blocked", "closed"),
        ("resolved", "in_progress"),
        ("closed", "open"),
    ],
)
def test_transition_command_rejects_forbidden_edges(source: str, target: str) -> None:
    with pytest.raises(ValidationError, match="transition"):
        TransitionInvestigation.model_validate(
            valid_transition_payload() | {"from_status": source, "to_status": target}
        )
```

The corresponding positive matrix is exactly `open -> in_progress|blocked`, `in_progress -> blocked|resolved`, `blocked -> in_progress|resolved`, and `resolved -> closed` only through `CloseInvestigation`.

- [ ] **Step 5: Implement and test terminal proposal/execution consistency**

```python
def test_approved_decision_requires_private_grant_and_rejection_forbids_it() -> None:
    approved = valid_decision_receipt(decision="approve", include_grant=True)
    rejected = valid_decision_receipt(decision="reject", include_grant=False)
    assert approved.grant is not None
    assert rejected.grant is None
    with pytest.raises(ValidationError, match="grant"):
        valid_decision_receipt(decision="reject", include_grant=True)
```

`ExecutionGrant` must be excluded from ordinary snapshot serialization helpers and from `operations.__all__`; trusted code imports it from the exact contracts module. `ProposalSnapshot` contains hashes and public preview only, never canonical payload bytes.

- [ ] **Step 6: Implement sanitized errors and prove secret-bearing inputs are absent**

```python
def test_operation_error_does_not_render_private_detail() -> None:
    error = ApprovalError("signature_invalid", retryable=False)
    rendered = f"{error!s}|{error!r}"
    assert error.reason_code == "signature_invalid"
    assert error.retryable is False
    assert "signature_invalid" not in rendered
    assert "nonce" not in rendered.casefold()
    assert "signature" not in rendered.casefold()
```

Constructors accept only `reason_code` and `retryable`; private exceptions remain chained internally and are never copied into the public message.

- [ ] **Step 7: Run and refactor the complete Task 1 slice**

Run:

```powershell
uv run pytest tests/unit/operations/test_contracts.py tests/unit/operations/test_commands.py tests/unit/operations/test_errors.py -q
uv run ruff check src/commerce_agent/operations tests/unit/operations
```

Expected: all Task 1 tests PASS; imports flow from contracts/commands to callers only; no persistence or orchestration import appears in `operations/contracts.py` or `operations/commands.py`.

**Completion standard:** all eight commands have exact typed fields, all lifecycle enums/refs are immutable, invalid state/field/time/Decimal/evidence shapes fail closed, public errors are stable and sanitized, and there is no generic batch/patch/write-SQL escape hatch.

### Task 2: Implement Canonical JSON, Approval Envelopes, and Deterministic Time/Nonce Ports

**Files:**
- Create: `src/commerce_agent/operations/_canonical.py`
- Create: `src/commerce_agent/operations/_approval.py`
- Create: `tests/unit/operations/test_canonical.py`
- Create: `tests/unit/operations/test_approval.py`

**Interfaces:**
- Consumes: Task 1 `OperationCommand`, `ProposalRef`, `ExecutionGrant`, actor IDs, and target versions.
- Produces: `CanonicalPayload(schema_version, canonical_bytes, sha256)`, `ApprovalEnvelope`, `Clock.now()`, `NonceSource.issue()`, `ApprovalKeyring.sign()`/`verify()`, `ApprovalService.issue_grant()`/`verify_grant()`.
- Dependency direction: `workflow.py` may depend on `_canonical` and `_approval`; these files may not depend on workflow, adapters, orchestration, or BIRD.

- [ ] **Step 1: Write fixed canonical and HMAC golden-vector tests**

```python
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from commerce_agent.operations._approval import ApprovalEnvelope, HmacApprovalKeyring
from commerce_agent.operations._canonical import canonical_json_bytes


def test_approval_envelope_matches_reviewed_golden_vector() -> None:
    envelope = ApprovalEnvelope(
        schema_version=1,
        proposal_id=UUID("00000000-0000-0000-0000-000000000010"),
        proposal_version=1,
        payload_sha256="a" * 64,
        requester_id=UUID(int=1),
        approver_id=UUID(int=2),
        target_versions={"investigation_task": 0},
        expires_at=datetime(2026, 9, 6, 4, 10, tzinfo=UTC),
        nonce="AQID",
    )
    canonical = canonical_json_bytes(envelope)
    signature = HmacApprovalKeyring({1: b"day4-test-key"}, active_version=1).sign(
        key_version=1,
        message=canonical,
    )

    assert canonical.decode() == (
        '{"approver_id":"00000000-0000-0000-0000-000000000002",'
        '"expires_at":"2026-09-06T04:10:00Z","nonce":"AQID",'
        '"payload_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"proposal_id":"00000000-0000-0000-0000-000000000010",'
        '"proposal_version":1,"requester_id":"00000000-0000-0000-0000-000000000001",'
        '"schema_version":1,"target_versions":{"investigation_task":0}}'
    )
    assert signature == "0334990d9927ca23561f2ad5c42f868ccd56e2239db7d6b1756b4ab2b8f409c4"
```

- [ ] **Step 2: Run the golden vector red**

Run: `uv run pytest tests/unit/operations/test_canonical.py::test_approval_envelope_matches_reviewed_golden_vector -q`

Expected: FAIL because canonical/approval modules do not exist.

- [ ] **Step 3: Implement a closed canonical projection, not generic object dumping**

```python
def canonical_json_bytes(value: ApprovalEnvelope | CanonicalCommand) -> bytes:
    projected = value.canonical_projection()
    return json.dumps(
        projected,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def normalized_decimal(value: Decimal, *, scale: int) -> str:
    if not value.is_finite():
        raise OperationContractError("decimal_not_finite", retryable=False)
    quantized = value.quantize(Decimal(1).scaleb(-scale))
    return format(quantized, f".{scale}f")
```

Every command has an explicit versioned projection. Do not use `model_dump()` as the signing format because field aliases/defaults can drift across Pydantic revisions.

- [ ] **Step 4: Add normalization and rejection slices one at a time**

```python
@pytest.mark.parametrize(
    "value",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_non_finite_decimal_is_rejected(value: Decimal) -> None:
    with pytest.raises(OperationContractError) as caught:
        normalized_decimal(value, scale=4)
    assert caught.value.reason_code == "decimal_not_finite"


def test_naive_datetime_is_rejected_instead_of_assuming_timezone() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        ApprovalEnvelope.model_validate(
            valid_envelope_payload() | {"expires_at": datetime(2026, 9, 6, 4, 10)}
        )
```

Also cover Unicode preservation, stable key ordering, lowercase UUIDs, UTC conversion, fixed `Z` precision, Decimal scale, schema-version change, target-version ordering, and command discriminator inclusion.

- [ ] **Step 5: Implement grant issue/verify with injected deterministic ports**

```python
class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FixedNonceSource:
    def issue(self) -> bytes:
        return bytes(range(32))
```

Production `SecretsNonceSource` uses `secrets.token_bytes(32)`. `ApprovalService.issue_grant()` encodes the nonce with URL-safe base64, stores/returns its SHA-256 digest separately, sets exactly `now + timedelta(minutes=10)`, and signs with the active key version. `verify_grant()` reconstructs the envelope from trusted stored proposal/decision data and uses `hmac.compare_digest`.

- [ ] **Step 6: Prove tamper, actor, key-version, expiry, and proposal binding failures**

```python
@pytest.mark.parametrize(
    ("field", "value", "reason_code"),
    [
        ("payload_sha256", "b" * 64, "signature_invalid"),
        ("approver_id", UUID(int=3), "grant_actor_mismatch"),
        ("proposal_version", 2, "signature_invalid"),
        ("key_version", 99, "approval_key_unavailable"),
    ],
)
def test_grant_changes_fail_closed(field: str, value: object, reason_code: str) -> None:
    service, stored, grant = approved_fixture()
    changed = grant.model_copy(update={field: value})
    with pytest.raises((ApprovalError, OperationAuthorizationError)) as caught:
        service.verify_grant(changed, stored=stored, actor=stored.approver)
    assert caught.value.reason_code == reason_code
```

Expiry comparison is `now >= expires_at`; proposal expiry is independently 24 hours. Failed verification occurs before any store execute call.

- [ ] **Step 7: Run and refactor Task 2**

Run:

```powershell
uv run pytest tests/unit/operations/test_canonical.py tests/unit/operations/test_approval.py -q
uv run ruff check src/commerce_agent/operations tests/unit/operations
```

Expected: golden vectors and all tamper/expiry/key/actor cases PASS; test key material appears only in unit fixtures; no secret is logged or serialized through public snapshots.

**Completion standard:** canonical payload bytes are deterministic and versioned, HMAC verification is constant-time, nonce entropy is 256 bits, time is injected, all binding failures occur before persistence, and fixed vectors catch Pydantic/serialization drift.

### Task 3: Add Provider-Neutral Trace Contracts, Redaction, and an In-Memory Adapter

**Files:**
- Create: `src/commerce_agent/trace/__init__.py`
- Create: `src/commerce_agent/trace/contracts.py`
- Create: `src/commerce_agent/trace/errors.py`
- Create: `src/commerce_agent/trace/module.py`
- Create: `src/commerce_agent/trace/_memory.py`
- Create: `tests/unit/trace/__init__.py`
- Create: `tests/unit/trace/test_contracts.py`
- Create: `tests/unit/trace/test_module.py`
- Create: `tests/unit/trace/test_memory.py`

**Interfaces:**
- Produces: `TraceEventType`, `TraceStatus`, `ScopedTraceEvent`, `TracePort.append(event)`, `TraceModule.append(event)`, and `InMemoryTraceStore`.
- Consumes: provider-neutral `RunScope`, attempt ID, stable event position, hashes, usage/cost summaries, evidence refs, operation public refs, and reason codes.
- Does not produce: Trace query access for Product models or any BIRD database credential/adapter binding.

- [ ] **Step 1: Write the failing scope/sequence/redaction tests**

```python
@pytest.mark.asyncio
async def test_trace_appends_monotonic_sanitized_events() -> None:
    store = InMemoryTraceStore()
    trace = TraceModule(store=store)
    first = scoped_event(sequence=0, event_type="investigation_plan_accepted")
    second = scoped_event(sequence=1, event_type="proposal_created")

    await trace.append(first)
    await trace.append(second)

    assert await store.events(first.run_scope, first.attempt_id) == (first, second)


def test_trace_contract_rejects_private_fields() -> None:
    with pytest.raises(ValidationError):
        ScopedTraceEvent.model_validate(
            scoped_event(sequence=0).model_dump() | {"signature": "private"}
        )
```

- [ ] **Step 2: Run the first Trace test red**

Run: `uv run pytest tests/unit/trace/test_module.py::test_trace_appends_monotonic_sanitized_events -q`

Expected: FAIL because the Trace package does not exist.

- [ ] **Step 3: Implement the small append seam and closed event schema**

```python
class TracePort(Protocol):
    async def append(self, event: ScopedTraceEvent) -> None:
        raise NotImplementedError


class TraceModule:
    def __init__(self, *, store: TracePort) -> None:
        self._store = store

    async def append(self, event: ScopedTraceEvent) -> None:
        validate_trace_event(event)
        await self._store.append(event)
```

`ScopedTraceEvent` fields are limited to RunScope, attempt, phase, sequence, UTC timestamp, node, event type, status, structured decision summary, model/config/tool hashes, sanitized usage/cost, evidence/query fingerprints, reason code, and public proposal/execution/audit refs. Summaries accept a closed Pydantic type, not arbitrary mappings.

- [ ] **Step 4: Add red/green cases for sequence and scope isolation**

```python
@pytest.mark.asyncio
async def test_duplicate_or_out_of_order_sequence_fails_without_append() -> None:
    store = InMemoryTraceStore()
    trace = TraceModule(store=store)
    await trace.append(scoped_event(sequence=0))
    with pytest.raises(TraceContractError) as caught:
        await trace.append(scoped_event(sequence=0))
    assert caught.value.reason_code == "trace_sequence_invalid"
    assert len(await store.events(TEST_SCOPE, TEST_ATTEMPT)) == 1
```

Different RunScope/attempt pairs maintain independent sequences. The store is append-only; no update/delete method is exposed.

- [ ] **Step 5: Add explicit privacy cases**

```python
@pytest.mark.parametrize(
    "summary",
    [
        "postgresql://writer:secret@127.0.0.1/db",
        "sk-private-token",
        "raw_seller_id=abc123",
        "reasoning_content=private",
        "approval_nonce=AQID",
    ],
)
def test_trace_rejects_sensitive_summary(summary: str) -> None:
    with pytest.raises((ValidationError, TraceContractError)):
        scoped_event(sequence=0, decision_summary=summary)
```

Use field allowlists plus bounded safe text validation; do not depend only on substring filtering. Hashes and opaque public refs are permitted.

- [ ] **Step 6: Run and refactor Task 3**

Run:

```powershell
uv run pytest tests/unit/trace -q
uv run ruff check src/commerce_agent/trace tests/unit/trace
```

Expected: scope, ordering, append-only, error, and privacy tests PASS.

**Completion standard:** `TraceModule.append()` is the only public behavior, events are scoped/ordered/sanitized, in-memory Trace supports deterministic tests, and nothing grants a model or BIRD runtime Trace query or Product PostgreSQL access.

### Task 4: Build the In-Memory Operation Store and the Three-Method Workflow

**Files:**
- Create: `src/commerce_agent/operations/_store.py`
- Create: `src/commerce_agent/operations/_memory.py`
- Create: `src/commerce_agent/operations/workflow.py`
- Modify: `src/commerce_agent/operations/__init__.py`
- Create: `tests/unit/operations/test_memory_store.py`
- Create: `tests/unit/operations/test_workflow_propose.py`
- Create: `tests/unit/operations/test_workflow_decide.py`
- Create: `tests/unit/operations/test_workflow_execute.py`

**Interfaces:**
- Consumes: Tasks 1-3 contracts, canonicalization, approval service, injected `Clock`, `OperationStore`, and `TraceModule`.
- Produces: `OperationWorkflow.propose()`, `.decide()`, `.execute()` and an `InMemoryOperationStore` implementing the internal high-level transaction protocol.
- Internal store operations: `preview_command`, `create_or_get_proposal`, `decide_once`, `execute_once`, and `read_execution`. A `ReferenceValidator.validate_command(command, evidence_refs)` port supplies the generic reference gate here; Tasks 5-6 add SellerRef/backtest-specific validators. The store receives validated persistence DTOs and owns compare-and-set/transaction boundaries; workflow owns authorization, canonicalization, signing, and stable error mapping.

- [ ] **Step 1: Write proposal role/idempotency/version red tests**

```python
@pytest.mark.asyncio
async def test_only_analyst_can_create_one_immutable_proposal() -> None:
    workflow, store = workflow_fixture()
    request = valid_propose_request(actor=actor("analyst", 1), key="proposal-key-1")

    first = await workflow.propose(request)
    repeated = await workflow.propose(request)

    assert first == repeated
    assert first.status == "pending"
    assert first.proposal_ref.version == 1
    assert store.business_write_count == 0
    with pytest.raises(OperationAuthorizationError) as caught:
        await workflow.propose(request.model_copy(update={"actor": actor("approver", 2)}))
    assert caught.value.reason_code == "analyst_required"
```

- [ ] **Step 2: Run the proposal test red**

Run: `uv run pytest tests/unit/operations/test_workflow_propose.py::test_only_analyst_can_create_one_immutable_proposal -q`

Expected: FAIL because workflow/store do not exist.

- [ ] **Step 3: Implement propose in its exact fail-closed order**

```python
async def propose(self, request: ProposeRequest) -> ProposalSnapshot:
    self._require_role(request.actor, ActorRole.ANALYST, "analyst_required")
    validated = await self._references.validate_command(request.command, request.evidence_refs)
    preview = await self._store.preview_command(request.command, validated)
    canonical = canonical_command(request.command, preview.target_versions)
    draft = ProposalDraft.from_request(
        request=request,
        preview=preview,
        payload_sha256=canonical.sha256,
        expires_at=self._clock.now() + timedelta(hours=24),
    )
    snapshot = await self._store.create_or_get_proposal(draft)
    await self._trace_proposal(snapshot, request.actor)
    return snapshot
```

Reference validation and preview finish before persistence. Same requester/key/type/hash returns the same version; same key with any other binding raises `idempotency_conflict`. `revises` must identify the requester's latest pending version and atomically supersede it.

- [ ] **Step 4: Add revision and conflict slices**

```python
@pytest.mark.asyncio
async def test_revising_pending_proposal_supersedes_old_version() -> None:
    workflow, store = workflow_fixture()
    first = await workflow.propose(valid_propose_request(key="v1"))
    second = await workflow.propose(
        valid_propose_request(key="v2", revises=first.proposal_ref, title="Revised scope")
    )
    assert second.proposal_ref.proposal_id == first.proposal_ref.proposal_id
    assert second.proposal_ref.version == 2
    assert await store.status(first.proposal_ref) == "superseded"


@pytest.mark.asyncio
async def test_same_idempotency_key_with_changed_payload_fails() -> None:
    workflow, _store = workflow_fixture()
    await workflow.propose(valid_propose_request(key="same"))
    with pytest.raises(OperationConflictError) as caught:
        await workflow.propose(valid_propose_request(key="same", title="Changed"))
    assert caught.value.reason_code == "idempotency_conflict"
```

- [ ] **Step 5: Implement first-decision-wins and private grant issuance**

```python
@pytest.mark.asyncio
async def test_first_decision_wins_and_self_approval_never_creates_grant() -> None:
    workflow, store = workflow_fixture()
    proposal = await workflow.propose(valid_propose_request(actor=actor("analyst", 1)))
    with pytest.raises(OperationAuthorizationError) as caught:
        await workflow.decide(
            decision_request(proposal.proposal_ref, actor("approver", 1), "approve")
        )
    assert caught.value.reason_code == "self_approval_denied"
    assert await store.decision(proposal.proposal_ref) is None

    receipt = await workflow.decide(
        decision_request(proposal.proposal_ref, actor("approver", 2), "approve")
    )
    assert receipt.grant is not None
    with pytest.raises(ApprovalError) as duplicate:
        await workflow.decide(
            decision_request(proposal.proposal_ref, actor("approver", 3), "reject")
        )
    assert duplicate.value.reason_code == "proposal_already_decided"
```

Lock/read the proposal, lazily expire it when `now >= proposal.expires_at`, compare-and-set pending to approve/reject, and issue/store the nonce digest atomically with the approval decision. Rejection returns no grant.

- [ ] **Step 6: Implement execute idempotency, actor binding, and transaction outcome mapping**

```python
@pytest.mark.asyncio
async def test_execute_retry_returns_original_receipt_without_second_write() -> None:
    workflow, store, grant = approved_workflow_fixture()
    request = ExecuteRequest(actor=actor("approver", 2), grant=grant)

    first = await workflow.execute(request)
    repeated = await workflow.execute(request)

    assert repeated == first
    assert store.business_write_count == 1
    assert store.audit_count == 1
    assert store.nonce_claim_count == 1
```

Execute ordering is: read stored approved proposal/decision -> verify same approver -> rebuild/verify envelope/HMAC/expiry/payload/target bindings -> ask store to execute once -> append sanitized Trace after outcome. Known pre-commit/rollback failures map to retryable/final errors as specified; unknown commit outcome maps only to `OperationOutcomeUnknown` and includes a public execution lookup ref.

- [ ] **Step 7: Add the complete negative execution matrix**

Add one test and implementation slice per row:

| Case | Expected reason | Business writes | Audit events | Nonce consumed |
|---|---|---:|---:|---:|
| no decision/grant | `approval_required` | 0 | 0 | 0 |
| self approval | `self_approval_denied` | 0 | 0 | 0 |
| grant actor changed | `grant_actor_mismatch` | 0 | 0 | 0 |
| command/payload changed | `signature_invalid` | 0 | 0 | 0 |
| proposal expired before decision | `proposal_expired` | 0 | 0 | 0 |
| grant expired | `grant_expired` | 0 | 0 | 0 |
| nonce already committed | returns original receipt for same execution; otherwise `nonce_used` | 0 additional | 0 additional | 1 total |
| target version changed | `target_version_conflict` | 0 | 0 | 0 |
| handler constraint rollback | `command_failed_retryable` or exact final reason | 0 | 0 | 0 |
| commit response lost | `outcome_unknown` | at most 1 | at most 1 | at most 1 |

- [ ] **Step 8: Test all business state machines through the workflow**

```python
@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("open", "in_progress"),
        ("open", "blocked"),
        ("in_progress", "blocked"),
        ("blocked", "in_progress"),
        ("in_progress", "resolved"),
        ("blocked", "resolved"),
    ],
)
@pytest.mark.asyncio
async def test_investigation_transition_advances_exact_version(source: str, target: str) -> None:
    receipt, stored = await execute_transition_fixture(source, target)
    assert receipt.before_version + 1 == receipt.after_version
    assert stored.status == target
    assert stored.version == receipt.after_version
```

Add separate slices for assignment without status change, conclusion before close, close only from resolved, and linked risk disposition updated in the same composite transaction. Alert rule creation begins as enabled only after execution; an alert hit never creates a task without a new proposal/approval.

- [ ] **Step 9: Run and refactor Task 4**

Run:

```powershell
uv run pytest tests/unit/operations/test_memory_store.py tests/unit/operations/test_workflow_propose.py tests/unit/operations/test_workflow_decide.py tests/unit/operations/test_workflow_execute.py -q
uv run ruff check src/commerce_agent/operations tests/unit/operations
```

Expected: workflow/store/state/authorization/idempotency/rollback tests PASS; `business_write_count` remains zero for every negative path; Trace failures are classified without claiming a business rollback after a known database commit.

**Completion standard:** the in-memory path fully proves the three-method API, immutable proposal versions, first decision, one-time grant, same-actor execution, all three business state machines, idempotent recovery, and zero unauthorized business mutations.

### Task 5: Add Evidence-Bound Opaque SellerRef Issuance and Resolution

**Files:**
- Create: `src/commerce_agent/operations/_seller_refs.py`
- Modify: `src/commerce_agent/operations/contracts.py`
- Modify: `src/commerce_agent/operations/workflow.py`
- Create: `tests/unit/operations/test_seller_refs.py`
- Modify: `tests/unit/operations/test_workflow_propose.py`

**Interfaces:**
- Consumes: `SellerTargetRequest` containing validated metric/time/status/minimum-volume/anomaly facts and query-evidence digests, injected keyring/clock, and a private fixed-template `SellerIdentityStore.find_candidates(request)`/`iter_allowed_identities()` port.
- Produces: `SellerTargetResolver.find_candidates(request) -> tuple[SellerCandidateEvidence, ...]` with aggregate measures plus opaque refs, and internal `resolve(ref, expected_evidence_digest) -> PrivateSellerTarget` for proposal validation.
- Privacy boundary: `PrivateSellerTarget.seller_id` remains internal to `_seller_refs`, `_store`, and `_postgres`; it is never part of `operations.__all__`, a model tool DTO, Trace, audit payload, scenario result, or `ops_read` projection.

- [ ] **Step 1: Write the failing opacity and deterministic-resolution test**

```python
@pytest.mark.asyncio
async def test_seller_ref_is_opaque_and_resolves_one_evidence_bound_target() -> None:
    resolver = seller_resolver_fixture(
        observations=(private_seller_observation("seller-private-001", late_rate="0.30"),)
    )
    evidence_digest = "a" * 64

    candidates = await resolver.find_candidates(
        seller_target_request(evidence_digest=evidence_digest, anomaly_threshold="0.20")
    )
    reference = candidates[0].seller_ref
    resolved = await resolver.resolve(reference, expected_evidence_digest=evidence_digest)

    assert resolved.seller_id == "seller-private-001"
    assert "seller-private-001" not in reference.model_dump_json()
    assert reference.namespace == "product:seller-target"
```

- [ ] **Step 2: Run the SellerRef test red**

Run: `uv run pytest tests/unit/operations/test_seller_refs.py::test_seller_ref_is_opaque_and_resolves_one_evidence_bound_target -q`

Expected: FAIL because `SellerTargetResolver` does not exist.

- [ ] **Step 3: Implement a non-decodable keyed-digest token**

Use an explicit versioned token projection:

```python
payload = {
    "evidence_digest": target.evidence_digest,
    "expires_at": utc_z(expires_at),
    "key_version": self._keyring.active_version,
    "namespace": "product:seller-target",
    "schema_version": 1,
    "seller_digest": self._keyring.digest_identity(target.seller_id),
}
token = urlsafe_b64encode(canonical_json(payload) + b"." + signature).decode().rstrip("=")
```

The private helper issuing each candidate token receives the seller identity only from the fixed-template store. `SellerCandidateEvidence` exposes the SellerRef, numerator, denominator, normalized metric, observation range, and evidence digest, never the identity. The token contains only a keyed digest, not encrypted/encoded seller text. Resolution scans allowed identities from a fixed store method, recomputes digests, uses constant-time comparison, and requires exactly one match.

- [ ] **Step 4: Add fail-closed tamper/expiry/namespace/evidence/cardinality slices**

```python
@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        ("token", "seller_ref_invalid"),
        ("namespace", "seller_ref_namespace_mismatch"),
        ("evidence", "seller_ref_evidence_mismatch"),
        ("expired", "seller_ref_expired"),
        ("missing", "seller_ref_not_found"),
        ("multiple", "seller_ref_not_unique"),
    ],
)
@pytest.mark.asyncio
async def test_seller_ref_fail_closed(mutation: str, reason_code: str) -> None:
    resolver, reference, expected_digest = mutated_seller_ref_fixture(mutation)
    with pytest.raises(OperationContractError) as caught:
        await resolver.resolve(reference, expected_evidence_digest=expected_digest)
    assert caught.value.reason_code == reason_code
```

No resolver path performs trigram, prefix, vector, or model-based ID matching. Raw ID iteration remains inside the private adapter and never enters an exception.

- [ ] **Step 5: Make seller-risk proposal validation require the resolved private target**

```python
@pytest.mark.asyncio
async def test_open_seller_risk_case_ref_must_match_query_evidence() -> None:
    workflow, store = workflow_fixture()
    request = seller_risk_request(
        seller_ref=issued_seller_ref(evidence_digest="a" * 64),
        evidence_digest="b" * 64,
    )
    with pytest.raises(OperationContractError) as caught:
        await workflow.propose(request)
    assert caught.value.reason_code == "seller_ref_evidence_mismatch"
    assert store.proposal_count == 0
```

- [ ] **Step 6: Run and refactor Task 5**

Run:

```powershell
uv run pytest tests/unit/operations/test_seller_refs.py tests/unit/operations/test_workflow_propose.py -q
uv run ruff check src/commerce_agent/operations tests/unit/operations
```

Expected: all opaque reference, workflow binding, and privacy assertions PASS.

**Completion standard:** a fixed-template seller target query can return aggregate evidence with opaque expiring references, proposal validation can privately resolve exactly one seller, model-generated QueryEngine output cannot project seller identity, tampering/mismatch/cardinality failures create no proposal, and raw seller identity cannot cross the private adapter boundary.

### Task 6: Implement Deterministic Metric Alert Backtesting and Snapshot Binding

**Files:**
- Create: `src/commerce_agent/operations/_backtest.py`
- Modify: `src/commerce_agent/operations/contracts.py`
- Modify: `src/commerce_agent/operations/workflow.py`
- Create: `tests/unit/operations/test_backtest.py`
- Modify: `tests/unit/operations/test_workflow_propose.py`

**Interfaces:**
- Produces: `AlertMetric` (`late_delivery_rate`, `low_rating_rate`, `cancellation_rate`), `CalendarWindow` (`week`, `month`), `AlertComparator`, `AlertBacktestRequest`, `AlertWindowResult`, `AlertBacktestSnapshot`, `MetricAlertBacktester.run(request) -> AlertBacktestSnapshot`.
- Consumes: a versioned `MetricDefinitionPort`, fixed-template `AlertObservationPort`, and immutable resolved filters. It never consumes arbitrary SQL from a model.
- Binding: snapshot stores `rule_spec_sha256` over metric revision, grain, window, comparator, normalized Decimal threshold, minimum denominator, UTC range, and sorted filter refs.

- [ ] **Step 1: Write a failing calendar-window and exclusion test**

```python
@pytest.mark.asyncio
async def test_backtest_marks_low_sample_and_incomplete_windows_as_excluded() -> None:
    backtester = backtester_fixture(
        observations=(
            observation("2026-01-01", numerator=1, denominator=2, complete=True),
            observation("2026-02-01", numerator=8, denominator=10, complete=False),
            observation("2026-03-01", numerator=3, denominator=10, complete=True),
        )
    )
    result = await backtester.run(
        backtest_request(window="month", threshold="0.20", minimum_denominator=5)
    )

    assert [item.excluded_reason for item in result.windows] == [
        "below_minimum_denominator",
        "incomplete_window",
        None,
    ]
    assert [item.hit for item in result.windows] == [False, False, True]
```

- [ ] **Step 2: Run the window test red**

Run: `uv run pytest tests/unit/operations/test_backtest.py::test_backtest_marks_low_sample_and_incomplete_windows_as_excluded -q`

Expected: FAIL because the backtest module does not exist.

- [ ] **Step 3: Implement the three fixed metric definitions and non-overlapping windows**

```python
METRIC_DEFINITIONS = {
    AlertMetric.LATE_DELIVERY_RATE: MetricDefinition(
        revision="metric.late_delivery_rate.v1",
        numerator="delivered_after_estimate",
        denominator="delivered_orders",
        time_field="order_purchase_timestamp",
        allowed_grains=("global", "seller_state", "product_category"),
    ),
    AlertMetric.LOW_RATING_RATE: MetricDefinition(
        revision="metric.low_rating_rate.v1",
        numerator="review_score_lte_2",
        denominator="reviewed_orders",
        time_field="review_creation_date",
        allowed_grains=("global", "seller_state", "product_category"),
    ),
    AlertMetric.CANCELLATION_RATE: MetricDefinition(
        revision="metric.cancellation_rate.v1",
        numerator="cancelled_orders",
        denominator="placed_orders",
        time_field="order_purchase_timestamp",
        allowed_grains=("global", "customer_state"),
    ),
}
```

The observation port accepts only `AlertBacktestQuery` generated from these definitions and parameter values. Zero denominator, denominator below minimum, or incomplete windows set `hit=False`, `normalized_value=None`, and an exact excluded reason.

- [ ] **Step 4: Add Decimal, hash, filter, and wording slices**

```python
def test_rule_spec_hash_changes_for_every_semantic_input() -> None:
    baseline = backtest_request()
    baseline_hash = rule_spec_sha256(baseline)
    for changed in (
        baseline.model_copy(update={"threshold": Decimal("0.2100")}),
        baseline.model_copy(update={"minimum_denominator": 20}),
        baseline.model_copy(update={"window": "week"}),
        baseline.model_copy(update={"metric_revision": "metric.late_delivery_rate.v2"}),
    ):
        assert rule_spec_sha256(changed) != baseline_hash
```

Validate metric revision exactly, allowed grain, calendar-aligned start/end, start before end, bounded range, comparator, Decimal scale/range, minimum denominator, and resolved filter allowlist.

- [ ] **Step 5: Bind alert-rule proposals and hit-to-task proposals independently**

```python
@pytest.mark.asyncio
async def test_changed_alert_rule_requires_new_backtest_and_new_approval() -> None:
    workflow, store, snapshot = alert_workflow_fixture()
    changed = create_alert_rule_command(snapshot, threshold=Decimal("0.25"))
    with pytest.raises(OperationContractError) as caught:
        await workflow.propose(valid_propose_request(command=changed))
    assert caught.value.reason_code == "backtest_spec_mismatch"
    assert store.proposal_count == 0
```

`CreateInvestigationFromAlertHit` validates that the hit belongs to a successfully executed enabled rule and always creates a separate proposal lineage. Enabling the rule never carries authority to create a task.

- [ ] **Step 6: Run and refactor Task 6**

Run:

```powershell
uv run pytest tests/unit/operations/test_backtest.py tests/unit/operations/test_workflow_propose.py -q
uv run ruff check src/commerce_agent/operations tests/unit/operations
```

Expected: window, exclusion, metric, hash-binding, proposal, and language-boundary tests PASS.

**Completion standard:** the three reviewed metrics replay deterministic, non-overlapping calendar windows; exclusions cannot become hits; complete rule specs are hash-bound; enabled rule and alert-hit investigation use separate approvals; no predictive/causal claim appears in contracts.

### Task 7: Implement SqlReasoner Generation, Typed Repair, Fingerprints, and Budget Accounting

**Files:**
- Create: `src/commerce_agent/sql_reasoning/__init__.py`
- Create: `src/commerce_agent/sql_reasoning/contracts.py`
- Create: `src/commerce_agent/sql_reasoning/errors.py`
- Create: `src/commerce_agent/sql_reasoning/reasoner.py`
- Modify: `src/commerce_agent/context_builder/contracts.py`
- Modify: `src/commerce_agent/context_builder/profiles.py`
- Create: `tests/unit/sql_reasoning/__init__.py`
- Create: `tests/unit/sql_reasoning/test_contracts.py`
- Create: `tests/unit/sql_reasoning/test_reasoner.py`
- Create: `tests/unit/sql_reasoning/test_fingerprints.py`
- Modify: `tests/unit/context_builder/test_contracts.py`
- Modify: `tests/unit/context_builder/test_profiles.py`

**Interfaces:**
- Produces: `ProductDbError`, `SqlReasoningRequest`, `SqlFingerprint`, `SqlCandidate`, `SqlReasoningSummary`, `SqlNoProgress`, and `SqlReasoner.generate(request) -> SqlCandidate`.
- Consumes: `ContextBuilder`, Retail profile, `ModelGateway`, injected SQL parser/fingerprint policy, prior candidate/error/evidence, and a repair budget supplied by RetailGraph.
- Does not consume: QueryEngine connection/executor, operation store, ProductScenarioDriver, or any BIRD protocol/state.

- [ ] **Step 1: Write the failing first-generation test with FakeModel**

```python
@pytest.mark.asyncio
async def test_reasoner_generates_one_bound_candidate_and_reports_usage() -> None:
    gateway = FakeModel([sql_candidate_response("SELECT COUNT(*) AS order_count FROM retail.orders")])
    reasoner = sql_reasoner_fixture(gateway)
    request = sql_reasoning_request(step_id="baseline", latest_error=None, previous=None)

    candidate = await reasoner.generate(request)

    assert candidate.sql == "SELECT COUNT(*) AS order_count FROM retail.orders"
    assert candidate.step_id == "baseline"
    assert candidate.repair_number == 0
    assert candidate.structural_fingerprint.algorithm_version == "sql-fingerprint-v1"
    assert candidate.execution_fingerprint.digest != "0" * 64
    assert len(gateway.requests) == 1
```

- [ ] **Step 2: Run the first-generation test red**

Run: `uv run pytest tests/unit/sql_reasoning/test_reasoner.py::test_reasoner_generates_one_bound_candidate_and_reports_usage -q`

Expected: FAIL because `commerce_agent.sql_reasoning` does not exist.

- [ ] **Step 3: Add Product SQL prompt steps and strict candidate parsing**

Add `RETAIL_SQL_GENERATE` and `RETAIL_SQL_REPAIR` to `PromptStep`; only the Retail profile can register them. The model returns one tool call named `submit_sql_candidate` with:

```json
{"evidence_refs":["knowledge:metric.orders:v1"],"sql":"SELECT COUNT(*) AS order_count FROM retail.orders","step_id":"baseline","type":"submit_sql_candidate"}
```

Unknown fields, a mismatched step ID, missing required evidence, ordinary final prose, multiple tool calls, non-SQL actions, and a candidate with operation/write intent raise a stable `SqlReasoningContractError` without reaching QueryEngine.

- [ ] **Step 4: Implement structural and execution fingerprints from parsed PostgreSQL AST**

```python
def fingerprints(sql: str) -> tuple[SqlFingerprint, SqlFingerprint]:
    statement = parse_one(sql, read="postgres", error_level=ErrorLevel.RAISE)
    execution = canonical_ast_sql(statement)
    structural_tree = statement.copy().transform(replace_literal_with_typed_marker)
    structural = canonical_ast_sql(structural_tree)
    return (
        SqlFingerprint.for_text("structural", structural),
        SqlFingerprint.for_text("execution", execution),
    )
```

Both fingerprints include `algorithm_version="sql-fingerprint-v1"` and the installed SQLGlot version. SQL generation during fingerprinting sets unsupported handling to raise. Preserve literal type in structural markers so strings/numbers/dates cannot collapse incorrectly.

- [ ] **Step 5: Prove literal changes preserve structure but change execution**

```python
def test_literal_change_keeps_structure_and_changes_execution() -> None:
    first = fingerprints("SELECT COUNT(*) FROM retail.orders WHERE order_status = 'delivered'")
    second = fingerprints("SELECT COUNT(*) FROM retail.orders WHERE order_status = 'canceled'")
    assert first[0] == second[0]
    assert first[1] != second[1]
```

Add cases for dates, numeric thresholds, identifier changes, join changes, and SQLGlot/algorithm revision differences.

- [ ] **Step 6: Implement typed-error repair and no-progress semantics**

```python
@pytest.mark.asyncio
async def test_same_execution_and_same_evidence_stops_without_another_repair() -> None:
    gateway = FakeModel([sql_candidate_response(REPEATED_SQL)])
    reasoner = sql_reasoner_fixture(gateway)
    request = sql_reasoning_request(
        previous=sql_candidate(REPEATED_SQL),
        latest_error=ProductDbError(
            source="postgres",
            reason_code="undefined_column",
            retryable=True,
            evidence_digest="b" * 64,
        ),
    )
    with pytest.raises(SqlNoProgress) as caught:
        await reasoner.generate(request)
    assert caught.value.reason_code == "sql_no_progress"
```

Repairs accept only sanitized `ProductDbError` fields, never raw database messages/SQL/DSN. RetailGraph supplies `repair_number` and refuses values above 3. The reasoner returns usage/cost/attempt summary for RetailGraph's single overall budget and does not own a separate resettable budget.

- [ ] **Step 7: Prove profile and BIRD isolation**

```python
def test_sql_reasoning_steps_exist_only_in_retail_profile(registry: ProfileRegistry) -> None:
    retail_steps = {rule.step for rule in registry.get("retail").inference_rules}
    assert {"retail_sql_generate", "retail_sql_repair"} <= {item.value for item in retail_steps}
    for key in ("bird_a", "bird_c"):
        assert not ({"retail_sql_generate", "retail_sql_repair"} & {
            item.value for item in (rule.step for rule in registry.get(key).inference_rules)
        })
```

- [ ] **Step 8: Run and refactor Task 7**

Run:

```powershell
uv run pytest tests/unit/sql_reasoning tests/unit/context_builder/test_contracts.py tests/unit/context_builder/test_profiles.py -q
uv run ruff check src/commerce_agent/sql_reasoning src/commerce_agent/context_builder tests/unit/sql_reasoning tests/unit/context_builder
```

Expected: generation, repair, malformed output, fingerprint, budget, no-progress, and profile isolation tests PASS using `FakeModel` only.

**Completion standard:** `SqlReasoner` generates/repairs constrained candidates, never executes SQL, consumes only typed errors, binds evidence/step/attempt, returns usage to RetailGraph, stops repeated evidence, supports at most three repairs, and introduces no Product capability into BIRD.

### Task 8: Deepen QueryEngine with Reconciliation and Reviewed ops_read Access

**Files:**
- Modify: `src/commerce_agent/query_engine/contracts.py`
- Modify: `src/commerce_agent/query_engine/engine.py`
- Modify: `src/commerce_agent/query_engine/_ast_policy.py`
- Modify: `src/commerce_agent/query_engine/__init__.py`
- Modify: `tests/unit/query_engine/test_contracts.py`
- Modify: `tests/unit/query_engine/test_engine.py`
- Create: `tests/unit/query_engine/test_reconciliation.py`
- Modify: `tests/integration/query_engine/test_query_engine_security.py`

**Interfaces:**
- Preserves: `QueryEngine.execute(QueryRequest) -> QueryResult` and all existing SQL safety behavior.
- Produces: `EvidenceResult`, discriminated `TotalRule`, `GrainRule`, `SetRule`, `NullRule`, `DecimalToleranceRule`, `ReconciliationRequest`, `ReconciliationCheck`, `ReconciliationResult`, and `QueryEngine.reconcile(request) -> ReconciliationResult`.
- Hides: comparison implementation inside QueryEngine; no public `ReconciliationChecker` abstraction.

- [ ] **Step 1: Write a failing total and Decimal reconciliation test**

```python
def test_reconcile_requires_total_and_decimal_rules_to_pass() -> None:
    engine = in_memory_query_engine()
    result = engine.reconcile(
        ReconciliationRequest(
            evidence=(
                evidence_result("baseline", [{"amount": "100.00"}]),
                evidence_result("parts", [{"amount": "60.00"}, {"amount": "40.00"}]),
            ),
            rules=(
                TotalRule(type="total", parent_ref="baseline", child_ref="parts", column="amount"),
                DecimalToleranceRule(
                    type="decimal_tolerance",
                    left_ref="baseline",
                    right_ref="parts",
                    column="amount",
                    absolute_tolerance=Decimal("0.01"),
                ),
            ),
        )
    )
    assert result.passed is True
    assert all(check.passed for check in result.checks)
```

- [ ] **Step 2: Run reconciliation red**

Run: `uv run pytest tests/unit/query_engine/test_reconciliation.py::test_reconcile_requires_total_and_decimal_rules_to_pass -q`

Expected: FAIL because reconciliation contracts/method do not exist.

- [ ] **Step 3: Implement the five closed rule handlers**

```python
handlers = {
    "total": self._reconcile_total,
    "grain": self._reconcile_grain,
    "set": self._reconcile_set,
    "null": self._reconcile_null,
    "decimal_tolerance": self._reconcile_decimal,
}
checks = tuple(handlers[rule.type](rule, indexed_evidence) for rule in request.rules)
return ReconciliationResult(passed=all(item.passed for item in checks), checks=checks)
```

Every check returns bounded actual/expected digests and a stable reason code, not full rows. Duplicate evidence IDs, missing columns, mixed scalar types, invalid grain keys, and non-finite Decimal values fail closed as contract errors.

- [ ] **Step 4: Add failure slices for join inflation, grain, set, NULL, and tolerance**

```python
@pytest.mark.parametrize(
    ("fixture_name", "reason_code"),
    [
        ("join_inflation", "total_mismatch"),
        ("duplicate_grain", "grain_not_unique"),
        ("set_difference", "set_mismatch"),
        ("unexpected_null", "null_policy_failed"),
        ("decimal_outside_tolerance", "decimal_tolerance_failed"),
    ],
)
def test_reconciliation_detects_invalid_evidence(fixture_name: str, reason_code: str) -> None:
    result = in_memory_query_engine().reconcile(reconciliation_fixture(fixture_name))
    assert result.passed is False
    assert reason_code in {check.reason_code for check in result.checks}
```

- [ ] **Step 5: Extend the AST policy only for four reviewed ops_read views**

Use a schema-qualified allowlist:

```python
ALLOWED_RELATIONS = {
    "retail": RETAIL_TABLE_COLUMNS,
    "ops_read": {
        "investigation_tasks": frozenset({"task_ref", "status", "assignee_ref", "version", "evidence_summary"}),
        "risk_annotations": frozenset({"risk_ref", "seller_ref", "status", "observed_from", "observed_to", "metric_ref", "metric_value"}),
        "enabled_metric_alert_rules": frozenset({"rule_ref", "metric_ref", "metric_revision", "grain", "comparator", "threshold", "window", "version"}),
        "enabled_metric_alert_hits": frozenset({"hit_ref", "rule_ref", "window_start", "window_end", "value", "coverage"}),
    },
}
```

Reject `ops.*`, unknown `ops_read` objects, raw audit/proposal/decision/nonce/canonical payload columns, and raw seller identity aliases before executor invocation.

Also trace AST projection lineage: any direct projection of seller/customer/order identity is rejected even when aliased. Seller risk drilldown obtains opaque candidates only through Task 5's fixed-template private target capability; a model-generated query cannot export an identity into `QueryResult`, checkpoint evidence, or a report.

- [ ] **Step 6: Preserve all existing SQL-sandbox regressions**

Run:

```powershell
uv run pytest tests/unit/query_engine -q
uv run pytest tests/integration/query_engine/test_query_engine_security.py -q --tb=line
```

Expected: unit tests PASS; PostgreSQL-marked tests remain skipped without the explicit switch; DDL/DML/CTE/lock/catalog/sequence/function/join/limit controls remain green.

- [ ] **Step 7: Refactor and run Task 8 lint**

Run: `uv run ruff check src/commerce_agent/query_engine tests/unit/query_engine tests/integration/query_engine`

Expected: PASS.

**Completion standard:** QueryEngine remains the sole Product SQL safety/execution/reconciliation module, all five reconciliation rule families are deterministic, only reviewed `ops_read` views are queryable, and neither raw ops nor seller identity can reach public query results.

### Task 9: Freeze Day 4 Retail Terminal Contracts, Step-Scoped Tools, and Profile Revisions

**Files:**
- Modify: `src/commerce_agent/orchestration/contracts.py`
- Modify: `src/commerce_agent/orchestration/tools.py`
- Modify: `src/commerce_agent/context_builder/contracts.py`
- Modify: `src/commerce_agent/context_builder/profiles.py`
- Create: `configs/model/run-profiles.v2.json`
- Create: `configs/model/prompt-policies.v2.json`
- Modify: `tests/unit/orchestration/test_contracts.py`
- Modify: `tests/unit/orchestration/test_tools.py`
- Modify: `tests/unit/orchestration/test_track_isolation.py`
- Modify: `tests/unit/context_builder/test_profiles.py`

**Interfaces:**
- Produces: `ClarificationSlot`, `ClarificationItem`, `ClarificationRequest`, `ConfirmedInvestigationFacts`, `QueryPlanStep`, `InvestigationPlan`, `InvestigationEvidence`, `InvestigationLimitation`, `InvestigationReport`, and discriminated `RetailTerminal`.
- Changes: `RetailRunOutcome.status` becomes `needs_input | completed | stopped`; `terminal` is exactly one `ClarificationRequest` for `needs_input` or one `InvestigationReport` for `completed`; `stop` exists only for `stopped`.
- Produces closed model actions: `retrieve_retail_knowledge`, `resolve_business_value`, `request_clarification`, `submit_investigation_plan`, `submit_sql_candidate`, `propose_operation`, and `submit_investigation_report`.
- Removes from the Day 4 model catalog: direct `execute_readonly_sql`. RetailGraph, not the model, invokes SqlReasoner and QueryEngine after accepting a plan.

- [ ] **Step 1: Write failing terminal-union and hard-slot tests**

```python
def test_retail_terminal_status_matches_typed_value() -> None:
    clarification = ClarificationRequest(
        conversation_id="retail-conversation-1",
        request_id=UUID(int=10),
        items=(
            ClarificationItem(
                slot="gmv_metric_definition",
                question="Which approved GMV definition should be used?",
                allowed_values=("item_amount", "payment_amount"),
            ),
        ),
    )
    outcome = retail_outcome(status="needs_input", terminal=clarification, stop=None)
    assert outcome.terminal.type == "clarification_request"
    with pytest.raises(ValidationError, match="status"):
        retail_outcome(status="completed", terminal=clarification, stop=None)


def test_plan_rejects_missing_required_gmv_slot() -> None:
    with pytest.raises(ValidationError, match="gmv_metric_definition"):
        investigation_plan(confirmed_slots={"time_range", "grain"})
```

- [ ] **Step 2: Run the terminal contract red**

Run: `uv run pytest tests/unit/orchestration/test_contracts.py::test_retail_terminal_status_matches_typed_value -q`

Expected: FAIL because the Day 4 terminal contracts are absent.

- [ ] **Step 3: Implement typed clarification, plan, and report contracts**

Use this closed set of slots:

```python
class ClarificationSlot(StrEnum):
    GMV_METRIC_DEFINITION = "gmv_metric_definition"
    VALID_ORDER_STATUSES = "valid_order_statuses"
    TIME_FIELD = "time_field"
    TIME_RANGE = "time_range"
    ANALYSIS_GRAIN = "analysis_grain"
    MINIMUM_ORDER_COUNT = "minimum_order_count"
    ANOMALY_THRESHOLD = "anomaly_threshold"
    BUSINESS_VALUE = "business_value"
```

`InvestigationPlan` contains confirmed facts, exactly one baseline step, at least one drilldown step, required evidence refs, explicit reconciliation rules, and stop conditions. Steps contain questions/measures/grains/references only, never SQL, handler, adapter, URL, DSN, role, or credential fields. `InvestigationReport` contains conclusion claims tied to evidence IDs, limitations, reconciliation refs, optional proposal refs, and an explicit sensitivity comparison when risk status is used.

- [ ] **Step 4: Add report-evidence and no-silent-GMV/risk-exclusion slices**

```python
def test_report_cannot_complete_with_unreferenced_claim() -> None:
    with pytest.raises(ValidationError, match="evidence"):
        investigation_report(
            claims=(claim("GMV changed", evidence_refs=("query:missing",)),),
            available_evidence=(evidence("query:baseline"),),
        )


def test_risk_filtered_report_requires_explicit_sensitivity_comparison() -> None:
    with pytest.raises(ValidationError, match="sensitivity"):
        investigation_report(
            selected_risk_status="confirmed",
            sensitivity_comparison=None,
        )
```

- [ ] **Step 5: Add step-scoped tool selection to profile contracts**

Extend each `InferenceRule` with an exact `tool_names` tuple. ContextBuilder selects only that tuple for the requested step and includes it in `tool_hash`/`config_hash`.

| Retail step | Allowed model tools |
|---|---|
| `retail_decide` | `retrieve_retail_knowledge`, `resolve_business_value`, `request_clarification`, `submit_investigation_plan` |
| `retail_clarify` | `request_clarification` |
| `retail_sql_generate` | `submit_sql_candidate` |
| `retail_sql_repair` | `submit_sql_candidate` |
| `retail_report` | `propose_operation`, `submit_investigation_report` |

BirdA and BirdC retain their existing revision-v1 logical tool sets, policies, namespaces, inference, and stop semantics. The v2 loader verifies that no Retail tool appears in either BIRD step and that no BIRD tool appears in a Retail step.

- [ ] **Step 6: Create canonical v2 config documents and test exact cross-file references**

```python
def test_day4_retail_profile_has_only_reviewed_step_tools(registry: ProfileRegistry) -> None:
    expected = {
        "retail_decide": {
            "retrieve_retail_knowledge",
            "resolve_business_value",
            "request_clarification",
            "submit_investigation_plan",
        },
        "retail_clarify": {"request_clarification"},
        "retail_sql_generate": {"submit_sql_candidate"},
        "retail_sql_repair": {"submit_sql_candidate"},
        "retail_report": {"propose_operation", "submit_investigation_report"},
    }
    assert registry.step_tools("retail") == expected
    assert "execute_readonly_sql" not in registry.tool_names("retail")
    assert "approve_operation" not in registry.tool_names("retail")
    assert "execute_operation" not in registry.tool_names("retail")
```

Use canonical one-line JSON plus newline, `run-profiles-v2`, `retail-profile-v2`, `retail-policy-v2`, and `retail-tools-v2`. Keep v1 files as immutable historical evidence; do not overwrite them.

- [ ] **Step 7: Implement closed tool DTO parsing with trusted actor injection**

```python
@pytest.mark.asyncio
async def test_propose_tool_uses_request_actor_not_model_arguments() -> None:
    dispatcher, workflow = dispatcher_fixture(actor=actor("analyst", 1))
    call = tool_call(
        "propose_operation",
        {"command": valid_create_task().model_dump(mode="json"), "actor": actor("approver", 9).model_dump(mode="json")},
    )
    with pytest.raises(ToolContractError) as caught:
        await dispatcher.execute(TEST_SCOPE, TEST_ATTEMPT, call)
    assert caught.value.reason_code == "invalid_tool_arguments"
    assert workflow.propose_calls == []
```

The valid `propose_operation` arguments contain only one command, evidence refs, idempotency key, and optional revises ref. Dispatcher obtains ActorContext from its trusted constructor/run context. No tool DTO contains decision, grant, approval identity, signature, nonce, SQL executor, adapter, or handler selection.

- [ ] **Step 8: Prove all tool schemas reject unknown fields and cross-step calls**

```python
@pytest.mark.parametrize(
    "name",
    [
        "request_clarification",
        "submit_investigation_plan",
        "submit_sql_candidate",
        "propose_operation",
        "submit_investigation_report",
    ],
)
def test_day4_tool_schema_is_closed(name: str, registry: ProfileRegistry) -> None:
    schema = registry.schema_for("retail", name)
    assert schema["additionalProperties"] is False
```

At runtime, validate the call name against the active prompt step before parsing arguments; a catalog-known tool used in the wrong step returns `tool_not_allowed_for_step`.

- [ ] **Step 9: Run and refactor Task 9**

Run:

```powershell
uv run pytest tests/unit/orchestration/test_contracts.py tests/unit/orchestration/test_tools.py tests/unit/orchestration/test_track_isolation.py tests/unit/context_builder/test_profiles.py -q
uv run ruff check src/commerce_agent/orchestration src/commerce_agent/context_builder tests/unit/orchestration tests/unit/context_builder
```

Expected: terminal, tool, config, hash, and isolation tests PASS; legacy v1 configs remain unchanged; all tests are offline.

**Completion standard:** missing hard slots cannot form a plan, missing evidence/reconciliation cannot form a completed report, model tools are step-scoped and closed, Retail has no direct SQL execution or approval capability, and BirdA/BirdC logical contracts remain unchanged.

### Task 10: Rebuild RetailGraph Around Clarify, Plan, Reason, Query, Reconcile, Propose, and Report Nodes

**Files:**
- Modify: `src/commerce_agent/orchestration/_checkpoint.py`
- Modify: `src/commerce_agent/orchestration/retail_graph.py`
- Modify: `src/commerce_agent/orchestration/tools.py`
- Modify: `tests/unit/orchestration/test_checkpoint.py`
- Modify: `tests/unit/orchestration/test_retail_graph.py`
- Modify: `tests/integration/orchestration/test_retail_public_tools.py`
- Create: `tests/unit/orchestration/test_retail_day4_flow.py`
- Create: `tests/unit/orchestration/test_retail_day4_privacy.py`

**Interfaces:**
- Consumes: ContextBuilder/ModelGateway, KnowledgeModule, BusinessValueResolver, SqlReasoner, QueryEngine, SellerTargetResolver, MetricAlertBacktester, `OperationWorkflow.propose`, TraceModule, checkpointer, and provider turn store.
- Produces: `RetailGraph.run(RetailRunRequest) -> RetailRunOutcome`; no new public orchestration method.
- Revisions: `RETAIL_STATE_SCHEMA_REVISION="retail-state-v2"`, `RETAIL_NODE_REVISION="retail-nodes-v2"`; v1 checkpoints fail with `state_schema_mismatch`/`node_revision_mismatch` and are never silently upgraded.

- [ ] **Step 1: Write a failing multi-slot clarification/new-attempt test**

```python
@pytest.mark.asyncio
async def test_missing_hard_slots_returns_one_multi_item_clarification() -> None:
    graph, gateway = retail_graph_fixture(
        model_outputs=[clarification_call("gmv_metric_definition", "time_field", "minimum_order_count")]
    )
    first = await graph.run(retail_request(attempt_id=UUID(int=10), confirmed_facts=()))

    assert first.status == "needs_input"
    assert tuple(item.slot for item in first.terminal.items) == (
        "gmv_metric_definition",
        "time_field",
        "minimum_order_count",
    )
    assert first.attempt_id == UUID(int=10)
    assert len(gateway.requests) == 1

    second = await graph.run(
        retail_request(
            attempt_id=UUID(int=11),
            confirmed_facts=answers_for(first.terminal),
        )
    )
    assert second.attempt_id == UUID(int=11)
```

- [ ] **Step 2: Run the Day 4 graph test red**

Run: `uv run pytest tests/unit/orchestration/test_retail_day4_flow.py::test_missing_hard_slots_returns_one_multi_item_clarification -q`

Expected: FAIL because the existing graph cannot return `needs_input` or Day 4 terminal contracts.

- [ ] **Step 3: Define the v2 checkpoint state explicitly**

Persist JSON-like values only:

```python
class RetailGraphStateV2(TypedDict):
    state_schema_revision: str
    node_revision: str
    run_scope_json: str
    attempt_id: str
    current_node: str
    clarification_count: int
    replan_count: int
    repair_count: int
    model_call_count: int
    tool_call_count: int
    confirmed_facts_json: str
    investigation_plan_json: str | None
    pending_plan_step_ids: list[str]
    sql_candidate_json: str | None
    query_evidence_json: str
    reconciliation_json: str | None
    proposal_refs_json: str
    terminal_json: str | None
    stop_reason_json: str | None
    pending_tool_batch_json: str | None
    provider_history_json: str
    revisions_json: str
    cleanup_complete: bool
```

`revisions_json` binds config/prompt/tool/index/Knowledge/catalog/fingerprint revisions. No Actor authentication ref, grant, signer/key, nonce, DSN, adapter, callable, raw seller identity, private provider payload, or BIRD state is serializable into this state.

- [ ] **Step 4: Build the node graph and deterministic routing**

Use these node names and transitions:

```text
validate_request -> collect_context -> call_control_model -> validate_control_action
  request_clarification -> finalize_needs_input
  submit_investigation_plan -> prepare_plan_step
prepare_plan_step -> sql_reason -> checkpoint_candidate -> execute_query
execute_query -> repair_or_record
  retryable ProductDbError -> sql_reason (repair_count + 1)
  evidence -> next_step_or_reconcile
next_step_or_reconcile -> prepare_plan_step | reconcile
reconcile -> prepare_report
prepare_report -> call_report_model -> validate_report_action
  propose_operation -> create_proposal -> prepare_report
  submit_investigation_report -> finalize_completed
any stable stop -> finalize_stopped
finalize_* -> commit terminal checkpoint -> delete exact private turns -> END
```

Seller-target and backtest plan steps call their internal Product capabilities from graph code. The model never selects adapters or passes SQL to QueryEngine directly.

- [ ] **Step 5: Enforce global budgets and checkpoint-before-side-effect order**

```python
class RetailLoopPolicy(BaseModel, frozen=True, extra="forbid"):
    max_model_calls: int = Field(default=6, ge=1, le=6)
    max_tool_calls: int = Field(default=12, ge=1, le=12)
    max_replans: int = Field(default=2, ge=0, le=2)
    max_repairs: int = Field(default=3, ge=0, le=3)
    max_clarifications: int = Field(default=2, ge=0, le=2)
```

Count all SqlReasoner model calls in `model_call_count`; do not reset them inside the reasoner. Checkpoint an accepted plan before its first query, each SQL candidate before QueryEngine, and each proposal idempotency key/ref before the next model call.

- [ ] **Step 6: Prove resume does not duplicate model/query/proposal work**

```python
@pytest.mark.parametrize(
    "interrupt_after",
    ["checkpoint_candidate", "execute_query", "create_proposal", "commit_terminal"],
)
@pytest.mark.asyncio
async def test_resume_is_equivalent_and_idempotent(interrupt_after: str) -> None:
    graph, dependencies = resumable_graph_fixture(interrupt_after)
    request = complete_investigation_request()
    interrupted = await graph.run(request)
    resumed = await graph.run(request)
    uninterrupted = await uninterrupted_graph_fixture().run(request)

    assert resumed == uninterrupted
    assert dependencies.proposal_store.unique_business_writes <= 1
    assert dependencies.query_port.duplicate_execution_fingerprints == ()
```

Same-attempt resume requires byte-identical request and revisions. A user clarification reply uses a new attempt with the same conversation subject and confirmed facts carried explicitly; it never mutates or resumes the prior terminal checkpoint.

- [ ] **Step 7: Prove terminal/private cleanup ordering**

```python
@pytest.mark.asyncio
async def test_cleanup_failure_retries_finalize_only() -> None:
    graph, dependencies = graph_with_one_cleanup_failure()
    first = await graph.run(complete_investigation_request())
    assert first.status == "stopped"
    assert first.stop.reason_code == "private_turn_cleanup_failed"

    second = await graph.run(complete_investigation_request())
    assert second.status == "completed"
    assert dependencies.gateway.call_count == dependencies.calls_before_cleanup_failure
    assert dependencies.query_port.call_count == dependencies.queries_before_cleanup_failure
    assert dependencies.workflow.propose_count == dependencies.proposals_before_cleanup_failure
```

The terminal checkpoint is durable before cleanup. A cleanup retry cannot repeat model calls, SQL, proposal creation, decisions, executions, or business writes.

- [ ] **Step 8: Add graph privacy and capability-denial tests**

```python
def test_retail_graph_constructor_has_no_approval_or_reset_capability() -> None:
    parameters = inspect.signature(RetailGraph).parameters
    assert "operation_workflow" in parameters
    assert "approval_signer" not in parameters
    assert "approval_store" not in parameters
    assert "reset_port" not in parameters


def test_checkpoint_state_rejects_sensitive_keys() -> None:
    encoded = canonical_checkpoint_json(valid_day4_state())
    for key in ("grant", "nonce", "signature", "hmac", "dsn", "seller_id", "reasoning_content"):
        assert key not in encoded.casefold()
```

The workflow dependency is exposed to graph through a proposal-only protocol containing only `propose`; Python type and runtime adapter tests reject access to `decide`/`execute`.

- [ ] **Step 9: Run and refactor Task 10**

Run:

```powershell
uv run pytest tests/unit/orchestration tests/integration/orchestration/test_retail_public_tools.py -q --tb=line
uv run ruff check src/commerce_agent/orchestration tests/unit/orchestration tests/integration/orchestration
```

Expected: all offline graph/checkpoint/tool/privacy/isolation tests PASS; PostgreSQL-marked cases stay skipped; no provider call occurs outside FakeModel.

**Completion standard:** RetailGraph enforces typed clarification/plan/report, owns budgets and node recovery, checkpoints before costly/side-effect boundaries, can only propose operations, produces the exact six stop semantics plus `needs_input`, and never leaks approval/private/seller/BIRD state.

### Task 11: Create ProductScenarioDriver Contracts, Serial Runner, Reset Port, and Assertions

**Files:**
- Create: `src/commerce_agent/product_eval/__init__.py`
- Create: `src/commerce_agent/product_eval/contracts.py`
- Create: `src/commerce_agent/product_eval/driver.py`
- Create: `src/commerce_agent/product_eval/_reset.py`
- Create: `tests/support/__init__.py`
- Create: `tests/support/product_eval_failures.py`
- Create: `tests/unit/product_eval/__init__.py`
- Create: `tests/unit/product_eval/test_contracts.py`
- Create: `tests/unit/product_eval/test_driver.py`
- Create: `tests/unit/product_eval/test_failure.py`
- Create: `tests/unit/product_eval/test_privacy.py`

**Interfaces:**
- Produces: immutable/versioned `ProductScenario`, `ScriptedClarification`, `ScenarioActorStep`, `OperationExpectation`, `ReadbackExpectation`, `AuditExpectation`, `ScenarioCheck`, `ProductScenarioResult`, and `ProductScenarioDriver.run(scenario) -> ProductScenarioResult`.
- Consumes: public `RetailGraph.run`, `OperationWorkflow.decide/execute`, `QueryEngine.execute` for `ops_read`, trusted test actor adapter, deterministic clock, and private `ScenarioResetPort.reset(scenario_id, manifest_revision)`.
- Dependency direction: `product_eval` imports Product public interfaces; no production package imports `product_eval`. Reset/failure decorators are test/evaluation composition capabilities, never runtime feature flags.

- [ ] **Step 1: Write failing scenario contract and final-closed boundary tests**

```python
def test_scenario_requires_versioned_fixture_and_deterministic_assertions() -> None:
    scenario = ProductScenario.model_validate(load_fixture("seller-risk-investigation-v1"))
    assert scenario.fixture_revision == "day4-development-v1"
    assert scenario.required_clarification_slots
    assert scenario.operation_expectations
    assert scenario.audit_expectations


def test_development_fixture_rejects_final_closed_classification() -> None:
    payload = load_fixture("seller-risk-investigation-v1") | {"visibility": "final_closed"}
    with pytest.raises(ValidationError, match="visibility"):
        ProductScenario.model_validate(payload)
```

- [ ] **Step 2: Run the scenario contract red**

Run: `uv run pytest tests/unit/product_eval/test_contracts.py::test_scenario_requires_versioned_fixture_and_deterministic_assertions -q`

Expected: FAIL because `product_eval` does not exist.

- [ ] **Step 3: Implement the immutable scenario schema**

Every scenario defines exactly:

```text
scenario_id, fixture_revision, visibility=development|regression,
initial_question, scripted_clarifications, analyst_actor_ref, approver_actor_ref,
clock_steps, ops_initial_state, reset_manifest_revision,
gold_tables, gold_columns, gold_values,
reference_query_assertions or database_assertions,
required_clarification_slots, forbidden_clarification_slots,
evidence_ids, operation_expectations, readback_expectations,
audit_expectations, allowed_failure_script, expected_terminal_status
```

The fixture contains opaque actor/seller refs only. A private reset manifest contains allowlisted table identifiers and scenario ID rules, not arbitrary SQL.

- [ ] **Step 4: Implement serial Driver orchestration through public seams**

```python
async def run(self, scenario: ProductScenario) -> ProductScenarioResult:
    async with self._serial_lock:
        await self._reset.reset(scenario.scenario_id, scenario.reset_manifest_revision)
        try:
            outcome = await self._run_conversation(scenario)
            operation_receipts = await self._run_trusted_actor_steps(scenario, outcome)
            readbacks = await self._readback(scenario)
            return self._validate(scenario, outcome, operation_receipts, readbacks)
        finally:
            await self._reset.reset(scenario.scenario_id, scenario.reset_manifest_revision)
```

An unregistered clarification receives exactly `"无法提供更多信息"` and increments `invalid_clarification_count`. Each scripted response creates a new attempt ID under the same conversation subject. The Driver does not reach into graph/store internals to make a scenario pass. A pre-run reset failure prevents Product execution; a post-run reset failure returns a failed cleanup check and never upgrades the scenario to passed or hides the already-observed business outcome.

- [ ] **Step 5: Implement deterministic validators, not language similarity**

```python
def test_driver_fails_when_operation_state_or_audit_ref_differs() -> None:
    result = validate_scenario(
        scenario=seller_risk_scenario(),
        observed=observed_result(risk_status="confirmed", audit_event_count=0),
    )
    assert result.status == "failed"
    assert {check.reason_code for check in result.checks} == {"audit_event_missing"}
```

Amounts compare after cent normalization or a fixture-declared Decimal tolerance; counts/statuses/versions are exact; sets/top-k/order use declared rules; text checks only required evidence IDs/limitations. Operation checks compare database/public store terminal state, version, one execution, and one audit event.

- [ ] **Step 6: Build adapter decorators for the four approved failure boundaries**

```python
class DropResponseAfterCommit:
    def __init__(self, inner: OperationStore, *, once_for: str) -> None:
        self._inner = inner
        self._once_for = once_for
        self._dropped = False

    async def execute_once(self, intent: ExecutionIntent) -> ExecutionReceipt:
        receipt = await self._inner.execute_once(intent)
        if not self._dropped and intent.failure_key == self._once_for:
            self._dropped = True
            raise CommitOutcomeUnknown("operation_commit_outcome_unknown")
        return receipt
```

Add equivalent decorators for proposal persist before call, proposal persist after commit/response loss, and execute call before commit. Production source has no failpoint setting, branch, environment variable, or monkeypatch dependency. The Driver receives already-composed public Product interfaces and never imports the test-only decorators or an internal store.

- [ ] **Step 7: Prove reset capability and import direction**

```python
def test_production_modules_do_not_import_product_eval() -> None:
    for source in production_python_sources(excluding="product_eval"):
        assert "commerce_agent.product_eval" not in imported_modules(source)


def test_driver_constructor_requires_reset_port_but_graph_does_not() -> None:
    assert "reset" in inspect.signature(ProductScenarioDriver).parameters
    assert "reset" not in inspect.signature(RetailGraph).parameters
    assert "reset" not in inspect.signature(OperationWorkflow).parameters
```

- [ ] **Step 8: Run and refactor Task 11**

Run:

```powershell
uv run pytest tests/unit/product_eval -q
uv run ruff check src/commerce_agent/product_eval tests/unit/product_eval
```

Expected: scenario schema, driver, validation, failure decorator, reset boundary, privacy, and import-direction tests PASS.

**Completion standard:** the Driver is serial and deterministic, uses only public Product interfaces plus a separately held reset port, returns exact checks/results, treats unregistered clarification consistently, exposes no reset/hidden answer to production, and has no model-judge dependency.

### Task 12: Prove All Three Loops and Recovery with Deterministic Fixtures

**Files:**
- Create: `tests/fixtures/product_eval/day4-development.v1.json`
- Create: `tests/fixtures/product_eval/day4-regression.v1.json`
- Create: `tests/unit/product_eval/test_day4_scenarios.py`
- Create: `tests/unit/product_eval/test_day4_recovery.py`
- Modify: `tests/unit/orchestration/test_retail_day4_flow.py`
- Modify: `tests/unit/orchestration/test_track_isolation.py`

**Interfaces:**
- Consumes: Tasks 1-11 public interfaces with in-memory adapters and FakeModel.
- Produces: reproducible scenario results for seller risk, metric alert-to-investigation, and ambiguous business investigation; no production interface.

- [ ] **Step 1: Add the seller-risk scenario and run it red**

The fixture asserts this exact observable chain:

```text
clarify valid order/time/minimum-volume/anomaly threshold
-> baseline query evidence
-> seller drilldown evidence
-> reconciliation passed
-> opaque SellerRef
-> OpenSellerRiskCase proposal
-> independent approval
-> one execution
-> one investigation + one under_investigation risk annotation + one audit
-> ops_read readback
-> explicit all-sellers versus selected-risk-status comparison
```

Test:

```python
@pytest.mark.asyncio
async def test_seller_risk_scenario_completes_approval_execution_readback() -> None:
    result = await in_memory_driver().run(load_day4_scenario("seller-risk-investigation-v1"))
    assert result.status == "passed"
    assert result.unauthorized_business_write_count == 0
    assert result.execution_count == 1
    assert result.audit_event_count == 1
    assert result.readback_count >= 2
    assert result.sensitivity_comparison_present is True
```

Run: `uv run pytest tests/unit/product_eval/test_day4_scenarios.py::test_seller_risk_scenario_completes_approval_execution_readback -q`

Expected first run: FAIL at the earliest unimplemented scenario transition; implement only that vertical slice and continue until green.

- [ ] **Step 2: Add the metric-alert-to-investigation scenario**

The scenario asserts deterministic monthly history, excluded low/empty/incomplete windows, one bound snapshot, one alert-rule proposal/approval/execution, `ops_read` rule/hit readback, then a different alert-hit-to-task proposal/approval/execution and task readback.

```python
@pytest.mark.asyncio
async def test_alert_hit_to_task_requires_second_approval() -> None:
    result = await in_memory_driver().run(load_day4_scenario("metric-alert-to-investigation-v1"))
    assert result.status == "passed"
    assert result.proposal_count == 2
    assert result.approval_count == 2
    assert result.execution_count == 2
    assert result.claims == ("historical_window_hits", "historical_window_coverage")
```

- [ ] **Step 3: Add the ambiguous operating-investigation scenario**

Require GMV definition, valid statuses, time field/range, grain, minimum order count, and anomaly threshold before plan acceptance. Then prove Knowledge/Resolver -> plan -> SqlReasoner -> QueryEngine -> drilldown -> reconciliation -> evidence report -> optional task proposal -> independent approval/execution -> readback.

```python
@pytest.mark.asyncio
async def test_ambiguous_gmv_scenario_never_infers_required_values() -> None:
    result = await in_memory_driver().run(load_day4_scenario("ambiguous-gmv-investigation-v1"))
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
```

- [ ] **Step 4: Add the negative authorization matrix to regression fixtures**

Each scenario starts from the same clean state and asserts zero successful business writes:

```python
@pytest.mark.parametrize(
    "scenario_id",
    [
        "unapproved-execution-v1",
        "self-approval-v1",
        "tampered-grant-v1",
        "expired-proposal-v1",
        "expired-grant-v1",
        "replayed-grant-v1",
        "target-version-conflict-v1",
    ],
)
@pytest.mark.asyncio
async def test_negative_operation_scenarios_write_nothing(scenario_id: str) -> None:
    result = await in_memory_driver().run(load_day4_regression(scenario_id))
    assert result.status == "passed"
    assert result.successful_business_write_count == 0
    assert result.successful_execution_audit_count == 0
```

- [ ] **Step 5: Add failure-injection recovery scenarios**

```python
@pytest.mark.parametrize(
    ("failure_point", "expected_proposals", "expected_writes"),
    [
        ("before_proposal_persist", 1, 0),
        ("after_proposal_commit_response_lost", 1, 0),
        ("before_execute_call", 1, 1),
        ("after_execute_commit_response_lost", 1, 1),
    ],
)
@pytest.mark.asyncio
async def test_failure_recovery_is_idempotent(
    failure_point: str,
    expected_proposals: int,
    expected_writes: int,
) -> None:
    result = await recovery_driver(failure_point).run(recovery_scenario())
    assert result.unique_proposal_count == expected_proposals
    assert result.unique_business_write_count == expected_writes
    assert result.duplicate_audit_count == 0
```

For pre-execute loss, the Driver retries the safe call using the same identity. For post-commit loss, it reads by proposal/execution identity and must not issue a second grant or blind write.

- [ ] **Step 6: Add explicit Product/BIRD import, profile, constructor, and fixture isolation checks**

```python
def test_bird_sources_have_no_day4_product_capability() -> None:
    forbidden = {
        "commerce_agent.operations",
        "commerce_agent.product_eval",
        "commerce_agent.query_engine",
        "commerce_agent.knowledge",
        "commerce_agent.value_resolver",
    }
    for source in bird_runtime_sources():
        imports = imported_modules(ast.parse(source.read_text(encoding="utf-8")))
        assert not any(
            module == denied or module.startswith(denied + ".")
            for module in imports
            for denied in forbidden
        )
```

Also assert BIRD tool catalogs contain none of `proposal`, `approval`, `operation`, `ops`, `risk`, `alert`, or Product SQL action names; BIRD request/state constructors accept no ActorContext, conversation state, Product store/checkpoint, DSN, or credential; Product profile contains no coin/simulator/submit-feedback semantics.

- [ ] **Step 7: Scan only the two approved fixture files and generated in-memory public results for privacy**

Use explicit paths, never a recursive repository/data scan. Assert absence of credential-like values, connection strings, raw seller IDs, full provider payload fields, private reasoning fields, reset secrets, and hidden-test classifications. Opaque `seller_ref` field names and guard assertions are allowed; values must match their public token pattern.

- [ ] **Step 8: Run and refactor the full offline Product core**

Run:

```powershell
uv run pytest tests/unit/operations tests/unit/sql_reasoning tests/unit/trace tests/unit/query_engine tests/unit/orchestration tests/unit/product_eval -q
uv run pytest -q --tb=line
uv run ruff check src tests scripts db/migrations
```

Expected: all default/offline tests PASS; every PostgreSQL and DeepSeek test is skipped with its explicit reason; all three deterministic loops pass; negative writes remain zero; no external connection or database mutation occurs.

**Completion standard:** three in-memory Product loops complete approval/execution/readback, regression attacks write nothing, failure decorators prove idempotent recovery, all required clarifications/reconciliations are enforced, and BIRD/privacy isolation remains a hard gate.

## Phase B: PostgreSQL Source Gates

### Task 13 / Gate M: Author the Day 4 Migration Source Without Applying It

**Entry condition:** Tasks 1-12 are green, the user has authorized implementation, and the reviewer separately opens the Migration Source Gate. This gate authorizes only source/tests for the migration.

**Files:**
- Create: `db/migrations/versions/0004_day4_product_operations.py`
- Create: `tests/unit/test_day4_operations_migration.py`

**Interfaces:**
- Consumes: Task 1 persistence contracts, Task 8 reviewed `ops_read` column names, Task 12 scenario IDs.
- Produces: Alembic revision `0004_day4_product_operations`, down revision `0003_day3_runtime_state`, exact object/constraint/function/ACL declarations.
- Does not perform: role creation, credential provisioning, migration upgrade/downgrade, data insert/update, or PostgreSQL connection.

**Exact schema/object contract:**

| Object | Required columns/constraints | Owner / exposure |
|---|---|---|
| `ops.operation_proposal` | `(proposal_id, proposal_version)` PK; requester, command type/schema version, canonical payload, payload hash, evidence refs, preview, target versions/hash, status, expiry, idempotency key, scenario ID, timestamps; unique requester/idempotency; latest-version/status checks | `ops_owner`; writer functions only; never `ops_read` |
| `ops.approval_decision` | exact proposal FK/unique, decision, requester, approver, reason, decided time; requester != approver | `ops_owner`; approval function only |
| `ops.approval_nonce` | nonce digest unique, proposal FK unique, approver, payload/target hash, key version, expiry, used time | `ops_owner`; no read view |
| `ops.command_execution` | execution ID PK, proposal FK unique, actor, command type, status, idempotency binding, before/after versions, result code, timestamps | `ops_owner`; executor receipt projection only |
| `ops.investigation_task` | task ref PK, status, priority, assignee ref, public summary, version, scenario ID, created/updated times | `ops_owner`; sanitized view |
| `ops.investigation_conclusion` | conclusion ref PK, task FK, code, public summary, evidence refs, task version, scenario ID, created time | `ops_owner`; sanitized through task view as required |
| `ops.risk_annotation` | risk ref PK, private seller ID, opaque seller ref, status, immutable observation window/metric/value/threshold/evidence, task FK, version, scenario ID | `ops_owner`; view excludes private seller ID |
| `ops.metric_alert_rule` | rule ref PK, enabled status, metric revision, grain/window/comparator/threshold/min denominator/filter refs/spec hash/backtest FK, version, scenario ID | `ops_owner`; enabled-only view |
| `ops.alert_backtest_result` | backtest ref PK, immutable spec/evidence hashes, bounded window JSON, coverage, scenario ID, created time | `ops_owner`; proposal writer controlled insert |
| `ops.audit_event` | event ID PK, command execution FK, event type/schema version, proposal/execution/actor/command/hash/public target refs/before-after/result/time, internal scenario ID; unique `(command_execution_id,event_type)` | `audit_owner`; no application update/delete/truncate |
| `app.product_trace_event` | RunScope digest, attempt, sequence, phase/node/type/status, safe JSON summary/hashes/reason/time, internal scenario ID; unique scope/attempt/sequence | `ops_owner`; trace writer append only |

Create security-barrier views exactly named `ops_read.investigation_tasks`, `ops_read.risk_annotations`, `ops_read.enabled_metric_alert_rules`, and `ops_read.enabled_metric_alert_hits`, matching Task 8's allowlist. Every view exposes only opaque/public refs and sanitized summaries. Do not expose proposal payloads, approval records, nonce/signature, raw audit, private actor/session data, or private seller identity.

Create `ops` and `trusted_schema` with `ops_owner`, and create `app` if absent with `ops_owner`; retain the existing `ops_read` schema and create only the four reviewed views in it. Transfer `ops.audit_event` and `trusted_schema.append_audit_event` to `audit_owner`; transfer control/command functions to `ops_owner`; transfer fixed seller/backtest read functions to existing NOLOGIN `retail_owner`. `scenario_reset_owner` owns only the fixed reset function and receives narrowly required DELETE privileges. It is NOLOGIN and is not an application role.

- [ ] **Step 1: Write a static migration test that records operations without connecting**

```python
def test_day4_migration_revision_and_object_manifest() -> None:
    module = load_migration()
    operations = RecordingOperations()
    module.op = operations
    module.upgrade()

    assert module.revision == "0004_day4_product_operations"
    assert module.down_revision == "0003_day3_runtime_state"
    assert {name for name, _items, _kwargs in operations.tables} == {
        "operation_proposal",
        "approval_decision",
        "approval_nonce",
        "command_execution",
        "investigation_task",
        "investigation_conclusion",
        "risk_annotation",
        "metric_alert_rule",
        "alert_backtest_result",
        "audit_event",
        "product_trace_event",
    }
```

- [ ] **Step 2: Run static migration red**

Run: `uv run pytest tests/unit/test_day4_operations_migration.py::test_day4_migration_revision_and_object_manifest -q`

Expected: FAIL because revision `0004_day4_product_operations` does not exist.

- [ ] **Step 3: Implement tables, checks, FKs, unique constraints, and indexes**

Include checks for lowercase SHA-256, positive versions, timezone-aware database timestamp types, proposal/execution/status enums, finite bounded numerics, requester/approver inequality, observation/rule range ordering, and scenario ID syntax. Use UUIDs supplied by the application; do not grant sequence access.

Add indexes for proposal requester/status/expiry, unused nonce expiry, execution proposal/status, task status/assignee, risk seller digest/status, alert rule status/spec, scenario IDs, audit execution/time, and Trace scope/attempt/sequence.

- [ ] **Step 4: Add static function-security tests before function bodies**

```python
def test_day4_migration_hardens_every_security_definer_function() -> None:
    sql = "\n".join(recorded_sql())
    required = {
        "trusted_schema.create_operation_proposal",
        "trusted_schema.record_approval_decision",
        "trusted_schema.store_alert_backtest",
        "trusted_schema.find_seller_target_candidates",
        "trusted_schema.read_late_delivery_backtest",
        "trusted_schema.read_low_rating_backtest",
        "trusted_schema.read_cancellation_backtest",
        "trusted_schema.append_audit_event",
        "trusted_schema.execute_open_seller_risk_case",
        "trusted_schema.execute_create_investigation_task",
        "trusted_schema.execute_create_investigation_from_alert_hit",
        "trusted_schema.execute_create_and_enable_metric_alert_rule",
        "trusted_schema.execute_assign_investigation",
        "trusted_schema.execute_transition_investigation",
        "trusted_schema.execute_add_investigation_conclusion",
        "trusted_schema.execute_close_investigation",
        "trusted_schema.reset_product_scenario",
    }
    assert all(name in sql for name in required)
    assert sql.count("SECURITY DEFINER") >= len(required)
    assert sql.count("SET search_path = trusted_schema, pg_temp") >= len(required)
    assert "EXECUTE ON ALL FUNCTIONS" not in sql
    assert "GRANT EXECUTE ON FUNCTION" in sql
```

- [ ] **Step 5: Implement typed functions with the exact transaction invariants**

Each command function takes typed scalar/JSON values, locks the exact proposal/execution/target rows, verifies stored approval/approver/payload hash/target hash/expiry, atomically claims one nonce digest with `UPDATE ... WHERE used_at IS NULL AND expires_at > transaction_timestamp() RETURNING`, applies one reviewed mutation, updates execution, and calls `trusted_schema.append_audit_event` before returning a sanitized receipt row.

Functions use schema-qualified objects and no dynamic SQL. `append_audit_event` verifies the execution/proposal/actor/current business version relationship before insert. Any exception rolls back nonce, business rows, successful execution, and audit together.

The four fixed Product read functions accept only typed date/metric/grain/filter parameters. Seller candidate reads return private seller identity only to `proposal_writer`; backtest reads return bounded aggregates. `proposal_writer` receives no general SELECT on `retail` and cannot submit SQL through these functions.

- [ ] **Step 6: Implement ownership, PUBLIC revocation, targeted grants, and views in the same migration transaction**

Required static assertions:

```python
def test_day4_migration_has_deny_by_default_acl_statements() -> None:
    sql = "\n".join(recorded_sql())
    assert "REVOKE ALL ON SCHEMA ops FROM PUBLIC" in sql
    assert "REVOKE ALL ON SCHEMA trusted_schema FROM PUBLIC" in sql
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA ops FROM PUBLIC" in sql
    assert "REVOKE ALL ON ALL SEQUENCES IN SCHEMA ops FROM PUBLIC" in sql
    assert "REVOKE ALL ON ops.audit_event FROM proposal_writer" in sql
    assert "GRANT SELECT ON ops_read.investigation_tasks" in sql
    assert "TO agent_reader" in sql
    assert "GRANT INSERT, UPDATE, DELETE ON ops.audit_event" not in sql
```

Grant `proposal_writer` only exact proposal/backtest/preview functions, `approval_writer` only exact decision function, `operation_executor` only exact command functions and receipt SELECT, `trace_writer` only app Trace INSERT, `agent_reader` only four reviewed views, and reset role only the exact reset function. Owners remain NOLOGIN via the separate role gate.

- [ ] **Step 7: Implement an explicit downgrade and static ordering checks**

Drop views/functions before dependent tables/schemas in reverse dependency order. Static tests verify the downgrade names every Day 4 object and does not touch `retail`, `knowledge`, `checkpoint`, or `model_state` schemas.

- [ ] **Step 8: Run only static migration/source quality checks**

Run:

```powershell
uv run pytest tests/unit/test_day4_operations_migration.py -q
uv run ruff check db/migrations/versions/0004_day4_product_operations.py tests/unit/test_day4_operations_migration.py
```

Expected: PASS without opening a connection. Do not run Alembic commands in this gate.

**Completion standard:** migration source has the exact object manifest, constraints, fixed functions, views, ownership/ACL statements, and downgrade; all static tests pass; no role, schema, table, function, credential, or data has been changed in PostgreSQL.

### Task 14 / Gate R: Author Credential and Role Provisioning Source Without Running It

**Entry condition:** Gate M is reviewed, and the reviewer separately opens the Role Provisioning Source Gate. This gate authorizes source/tests only.

**Files:**
- Create: `scripts/provision_day4_operations_env.py`
- Create: `scripts/bootstrap_day4_operations.py`
- Modify: `.env.example`
- Modify: `src/commerce_agent/config.py`
- Create: `tests/unit/scripts/test_provision_day4_operations_env.py`
- Create: `tests/unit/scripts/test_bootstrap_day4_operations.py`
- Modify: `tests/unit/test_config.py`

**Interfaces:**
- Produces empty config names for separate proposal, approval, executor, trace, and scenario-reset credentials/DSNs plus approval/seller-ref key v1 values.
- Produces `ensure_day4_operations_env(path, token_factory, key_factory) -> bool` and `bootstrap_day4_roles(admin_dsn, passwords) -> None`.
- Does not import or call Alembic, migration functions, workflow, scenario driver, or provider APIs.

- [ ] **Step 1: Write temp-directory credential provisioning tests**

```python
def test_provisioner_writes_distinct_values_atomically_without_printing_them(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    values = iter(f"generated-{index}" for index in range(1, 20))
    changed = ensure_day4_operations_env(
        env_path,
        token_factory=lambda: next(values),
        key_factory=lambda: next(values),
    )
    content = env_path.read_text(encoding="utf-8")
    assert changed is True
    assert len(parse_day4_values(content)) == 12
    assert len(set(parse_day4_values(content).values())) == 12
```

Tests use only a temporary `.env`; they never inspect the repository `.env`.

- [ ] **Step 2: Run provisioning source red**

Run: `uv run pytest tests/unit/scripts/test_provision_day4_operations_env.py::test_provisioner_writes_distinct_values_atomically_without_printing_them -q`

Expected: FAIL because the provisioning module does not exist.

- [ ] **Step 3: Implement atomic, fail-closed ignored-file provisioning**

The source recognizes exactly these names:

```text
PRODUCT_PROPOSAL_WRITER_PASSWORD / PRODUCT_PROPOSAL_DATABASE_DSN
PRODUCT_APPROVAL_WRITER_PASSWORD / PRODUCT_APPROVAL_DATABASE_DSN
PRODUCT_OPERATION_EXECUTOR_PASSWORD / PRODUCT_OPERATION_DATABASE_DSN
PRODUCT_TRACE_WRITER_PASSWORD / PRODUCT_TRACE_DATABASE_DSN
PRODUCT_SCENARIO_RESET_PASSWORD / PRODUCT_SCENARIO_RESET_DATABASE_DSN
PRODUCT_OPERATION_APPROVAL_HMAC_KEY_V1
PRODUCT_SELLER_REF_HMAC_KEY_V1
```

All values must be present and non-empty or all absent; partial state fails before write. Generated passwords/keys are distinct from each other and from known existing Product role secrets. DSNs bind exact role, `127.0.0.1:5432`, `commerce_analyst`, unique application name, and fixed search path. Write one same-directory temporary file and replace atomically. Stdout reports only changed/already-present state.

- [ ] **Step 4: Write role-bootstrap fake-cursor tests**

```python
def test_role_bootstrap_creates_hardened_non_inheriting_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor(existing_roles=set())
    monkeypatch.setattr("scripts.bootstrap_day4_operations.psycopg.connect", fake_connect(cursor))
    bootstrap_day4_roles("admin-dsn", distinct_passwords())
    sql = "\n".join(cursor.rendered_statements)

    for owner in ("ops_owner", "audit_owner", "scenario_reset_owner"):
        assert f'CREATE ROLE "{owner}" NOLOGIN NOINHERIT' in sql
    for writer in (
        "proposal_writer",
        "approval_writer",
        "operation_executor",
        "trace_writer",
        "product_scenario_reset",
    ):
        assert f'CREATE ROLE "{writer}" LOGIN NOINHERIT' in sql
    assert "SUPERUSER" not in sql.replace("NOSUPERUSER", "")
    assert "BYPASSRLS" not in sql.replace("NOBYPASSRLS", "")
```

- [ ] **Step 5: Implement idempotent role hardening only**

Owners (`ops_owner`, `audit_owner`, `scenario_reset_owner`): `NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`. Writers/reset: `LOGIN NOINHERIT` with the same negative capabilities, unique passwords, connection limit 4, `temp_file_limit='64MB'`, no role membership, database CONNECT only, and explicit database TEMP revocation. The script creates/updates roles but grants no schema/table/function privilege; migration owns object grants.

- [ ] **Step 6: Extend typed settings without reading process state at import**

Add optional `SecretStr` fields for the proposal, approval, operation, and Trace DSNs plus two HMAC keys. Deliberately omit the scenario-reset DSN from application `AppSettings`; only the evaluator composition root may pass it directly to `PostgresScenarioReset`. `load_settings(environment)` reads only the supplied mapping. Tests prove repr/model dumps do not reveal secret values, reset capability is absent, and missing Day 4 values remain valid for offline/default tests.

- [ ] **Step 7: Prove source commands do not compose with migration execution**

```python
def test_bootstrap_source_never_imports_or_invokes_alembic() -> None:
    source = Path("scripts/bootstrap_day4_operations.py").read_text(encoding="utf-8")
    assert "alembic" not in source.casefold()
    assert "upgrade" not in source.casefold()
    assert "0004_day4_product_operations" not in source
```

- [ ] **Step 8: Run only offline source tests and lint**

Run:

```powershell
uv run pytest tests/unit/scripts/test_provision_day4_operations_env.py tests/unit/scripts/test_bootstrap_day4_operations.py tests/unit/test_config.py -q
uv run ruff check scripts/provision_day4_operations_env.py scripts/bootstrap_day4_operations.py src/commerce_agent/config.py tests/unit/scripts tests/unit/test_config.py
```

Expected: PASS using temporary files/fake connections only. Do not run either new script against the repository or a database in this gate.

**Completion standard:** provisioning source is atomic and silent about values, roles are exact/non-inheriting/least-privileged, config secrets remain optional and redacted, migration execution is absent, and no repository secret or database role has been created/changed.

### Task 15: Implement Role-Bound PostgreSQL Adapters and Author Integration Contracts

**Files:**
- Create: `src/commerce_agent/operations/_postgres.py`
- Create: `src/commerce_agent/trace/_postgres.py`
- Modify: `src/commerce_agent/product_eval/_reset.py`
- Modify: `src/commerce_agent/config.py`
- Create: `tests/unit/operations/test_postgres_adapter.py`
- Create: `tests/unit/trace/test_postgres_adapter.py`
- Create: `tests/unit/product_eval/test_postgres_reset_adapter.py`
- Create: `tests/integration/operations/__init__.py`
- Create: `tests/integration/operations/test_operation_workflow.py`
- Create: `tests/integration/operations/test_operation_security.py`
- Create: `tests/integration/operations/test_operation_transactions.py`
- Create: `tests/integration/operations/test_operation_acls.py`
- Create: `tests/integration/trace/__init__.py`
- Create: `tests/integration/trace/test_product_trace.py`
- Create: `tests/integration/product_eval/__init__.py`
- Create: `tests/integration/product_eval/test_scenario_reset_security.py`
- Create: `tests/integration/orchestration/test_ops_readback.py`
- Modify: `tests/integration/conftest.py`

**Interfaces:**
- Produces: `PostgresOperationStore`, `PostgresSellerIdentityStore`, `PostgresAlertObservationStore`, `PostgresTraceStore`, and `PostgresScenarioReset` behind Tasks 3-11 protocols.
- Consumes: separate `SecretStr` DSNs whose parsed role/application/database/host/port/options match the exact adapter before any connection opens.
- Does not execute: migration, role bootstrap, real integration tests, reset, or business write under this task's source authorization.

- [ ] **Step 1: Write DSN identity rejection tests before adapter connection code**

```python
@pytest.mark.parametrize(
    ("adapter", "wrong_role"),
    [
        ("proposal", "approval_writer"),
        ("approval", "proposal_writer"),
        ("execute", "trace_writer"),
        ("trace", "operation_executor"),
        ("reset", "agent_reader"),
    ],
)
@pytest.mark.asyncio
async def test_adapter_rejects_wrong_database_identity_before_connect(
    adapter: str,
    wrong_role: str,
) -> None:
    connect = NeverConnect()
    instance = postgres_adapter_fixture(adapter, role=wrong_role, connect=connect)
    with pytest.raises((OperationInfrastructureError, TraceInfrastructureError)):
        await instance.open()
    assert connect.calls == []
```

- [ ] **Step 2: Run identity validation red**

Run: `uv run pytest tests/unit/operations/test_postgres_adapter.py::test_adapter_rejects_wrong_database_identity_before_connect -q`

Expected: FAIL because PostgreSQL adapters do not exist.

- [ ] **Step 3: Implement exact DSN/application/search-path validation and bounded transactions**

Required mappings:

| Adapter operation | Role | application_name | search_path |
|---|---|---|---|
| proposal/preview/backtest/seller identity | `proposal_writer` | `commerce_operation_proposal` | `ops,retail,trusted_schema,pg_catalog` |
| decision | `approval_writer` | `commerce_operation_approval` | `ops,trusted_schema,pg_catalog` |
| execute/read receipt | `operation_executor` | `commerce_operation_execute` | `trusted_schema,ops,pg_catalog` |
| Product Trace append | `trace_writer` | `commerce_product_trace` | `app,pg_catalog` |
| scenario reset | `product_scenario_reset` | `commerce_product_scenario_reset` | `trusted_schema,ops,pg_catalog` |

Every transaction sets local statement/lock/idle timeouts and uses parameter binding. The execute adapter invokes exactly one function from a static command-type mapping. No adapter accepts arbitrary function, table, SQL, or search path.

- [ ] **Step 4: Implement outcome classification at the transaction boundary**

```python
try:
    async with connection.transaction():
        receipt = await self._call_typed_function(cursor, intent)
except psycopg.errors.SerializationFailure as error:
    raise OperationInfrastructureError("operation_serialization_failure", retryable=True) from error
except CommitDeliveryUnknown as error:
    raise OperationOutcomeUnknown("operation_commit_outcome_unknown", retryable=True) from error
```

Pre-send/connect failures are retryable and known non-commits. Constraint/function failures use reviewed SQLSTATE/result-code mapping. A connection/read failure after commit send is outcome-unknown. Error text/repr never includes DSN, SQL, payload, nonce, signature, private seller ID, or server detail.

- [ ] **Step 5: Author the full PostgreSQL integration/ACL tests under the existing marker**

Every integration file sets `pytestmark = pytest.mark.postgres`. Cover:

```text
proposal writer: can use reviewed proposal/backtest functions; cannot decide, execute, reset, write business/audit, or read private runtime state
approval writer: can decide once; cannot propose, execute, reset, or DML business/audit
operation executor: can execute exact typed functions and read receipt; cannot direct DML business/audit, call proposal/decision/reset, or enumerate raw ops
trace writer: can append Product Trace only; cannot read/update/delete it or access ops/retail/knowledge/checkpoint/model_state
agent reader: can select four reviewed ops_read views; cannot access raw ops/audit/app Trace
reset role: can call exact reset function for exact scenario; cannot arbitrary DML, read secrets, or affect another scenario
owners: NOLOGIN; no membership granted to application roles
all roles: no TEMP, CREATE, sequence, audit UPDATE/DELETE/TRUNCATE, raw Knowledge, or unrelated runtime-state access
```

- [ ] **Step 6: Author transaction and concurrency tests**

```python
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_two_concurrent_execute_calls_commit_once(postgres_workflow: OperationWorkflow) -> None:
    request = await approved_execute_request(postgres_workflow)
    results = await asyncio.gather(
        postgres_workflow.execute(request),
        postgres_workflow.execute(request),
    )
    assert results[0] == results[1]
    assert await business_write_count(request.grant.proposal_ref) == 1
    assert await execution_audit_count(request.grant.proposal_ref) == 1
    assert await consumed_nonce_count(request.grant.proposal_ref) == 1
```

Add injected function/constraint failure proving nonce/business/execution/audit all roll back, target-version conflict before mutation, first-decision concurrency, payload tamper, expiry, invalid actor, and post-commit response loss followed by receipt readback.

- [ ] **Step 7: Extend the zero-session-leak fixture**

Add exactly the five Day 4 application names from the mapping above. After every integration test, the admin query must observe zero new Product application sessions. Keep `--tb=line` for every PostgreSQL command.

- [ ] **Step 8: Run unit adapter tests only; verify integration collection skips**

Run:

```powershell
uv run pytest tests/unit/operations/test_postgres_adapter.py tests/unit/trace/test_postgres_adapter.py tests/unit/product_eval/test_postgres_reset_adapter.py -q
uv run pytest tests/integration/operations tests/integration/trace tests/integration/product_eval tests/integration/orchestration/test_ops_readback.py -q --tb=line
uv run ruff check src/commerce_agent/operations src/commerce_agent/trace src/commerce_agent/product_eval tests/unit tests/integration
```

Expected: unit adapter tests PASS with fake connections; all new PostgreSQL integration tests are skipped with the required switch; no real connection, reset, or write occurs.

**Completion standard:** each adapter enforces its exact database identity and static operation set before connecting, integration/ACL/transaction contracts are fully authored and default-skipped, session leak names are registered, and no database state has changed.

## Phase C: Explicit Database Execution Gates

### Task 16 / Gate D: Provision Roles and Apply the Migration Only After Explicit Authorization

**Entry condition:** Tasks 13-15 source review is accepted and the user explicitly authorizes both Day 4 credential/role provisioning and migration application. Without that authorization, mark this gate `NOT RUN` and stop database work.

**Files changed by execution:** ignored local `.env` only through the reviewed provisioner, and Product PostgreSQL catalog/state through reviewed role/bootstrap/Alembic commands. No repository source file is edited in this gate.

- [ ] **Step 1: Re-run offline source gates before mutation**

Run:

```powershell
uv run pytest tests/unit/test_day4_operations_migration.py tests/unit/scripts/test_provision_day4_operations_env.py tests/unit/scripts/test_bootstrap_day4_operations.py tests/unit/operations/test_postgres_adapter.py -q
uv run ruff check src tests scripts db/migrations
```

Expected: PASS. Any failure closes Gate D before provisioning.

- [ ] **Step 2: Read-only preflight without opening or printing the secret file**

Run:

```powershell
docker compose --env-file .env ps
uv run --env-file .env alembic current
uv run --env-file .env alembic heads
```

Expected: Product PostgreSQL healthy on its pinned endpoint; current is `0003_day3_runtime_state`; sole head is `0004_day4_product_operations`. Do not continue if topology, current revision, or head differs.

- [ ] **Step 3: Provision missing ignored values through the reviewed atomic script**

Run only under the explicit Gate D authorization:

```powershell
uv run python scripts/provision_day4_operations_env.py
```

Expected stdout: one sanitized state line only. It must not print any value, DSN, key, or file content.

- [ ] **Step 4: Provision/harden roles before the migration references their ownership**

Run:

```powershell
uv run --env-file .env python scripts/bootstrap_day4_operations.py
```

Expected: exact owners/writers/reset roles created or hardened; no schema/table/function is created by this command.

- [ ] **Step 5: Apply exactly one reviewed migration**

Run:

```powershell
uv run --env-file .env alembic upgrade 0004_day4_product_operations
uv run --env-file .env alembic current
```

Expected: current revision is exactly `0004_day4_product_operations`. Do not automatically downgrade/retry on failure; capture the sanitized failure and stop for review.

- [ ] **Step 6: Run ACL, object, function-security, readback, and session tests before any positive business scenario**

Run:

```powershell
$env:COMMERCE_AGENT_RUN_POSTGRES_TESTS = '1'
uv run --env-file .env pytest tests/integration/operations/test_operation_acls.py tests/integration/operations/test_operation_security.py tests/integration/trace/test_product_trace.py tests/integration/product_eval/test_scenario_reset_security.py tests/integration/orchestration/test_ops_readback.py -m postgres -q --tb=line
$savedExit = $LASTEXITCODE
Remove-Item Env:COMMERCE_AGENT_RUN_POSTGRES_TESTS
if ($savedExit -ne 0) { throw "Day 4 database activation checks failed with exit code $savedExit" }
```

Expected: all tests PASS, owners are NOLOGIN, role matrix is exact, four views expose only reviewed columns, functions have safe definitions/ownership/grants, reset is scoped, and all Day 4 application sessions are closed.

**Completion standard:** the exact roles/objects/functions/views/ACLs exist at revision 0004 and security tests pass. This proves activation only; it does not authorize or claim a successful Product business write loop.

### Task 17 / Gate W: Run the Real PostgreSQL Seller-Risk Write/Readback Loop

**Entry condition:** Gate D passed and the user separately authorizes a real Product PostgreSQL write scenario. Without that explicit authorization, mark Gate W `NOT RUN`; Day 4 overall Gate cannot be PASS.

**Files:**
- Create during implementation before this gate: `tests/integration/product_eval/test_seller_risk_postgres_scenario.py`
- No source edits while the real run is in progress.

- [ ] **Step 1: Prove all negative paths on clean scenario-scoped state first**

Run:

```powershell
$env:COMMERCE_AGENT_RUN_POSTGRES_TESTS = '1'
uv run --env-file .env pytest tests/integration/operations/test_operation_security.py tests/integration/operations/test_operation_transactions.py -m postgres -q --tb=line
$savedExit = $LASTEXITCODE
Remove-Item Env:COMMERCE_AGENT_RUN_POSTGRES_TESTS
if ($savedExit -ne 0) { throw "Day 4 negative write gate failed with exit code $savedExit" }
```

Expected: unauthorized, self-approved, tampered, expired, replayed, and target-conflict successful business-write count is 0; rollback leaves nonce unused and no success audit; concurrent execute commits exactly once.

- [ ] **Step 2: Run exactly one deterministic positive seller-risk scenario**

Run:

```powershell
$env:COMMERCE_AGENT_RUN_POSTGRES_TESTS = '1'
$env:LANGGRAPH_STRICT_MSGPACK = 'true'
uv run --env-file .env pytest tests/integration/product_eval/test_seller_risk_postgres_scenario.py -m postgres -q --tb=line
$savedExit = $LASTEXITCODE
Remove-Item Env:COMMERCE_AGENT_RUN_POSTGRES_TESTS
Remove-Item Env:LANGGRAPH_STRICT_MSGPACK
if ($savedExit -ne 0) { throw "Day 4 real seller-risk scenario failed with exit code $savedExit" }
```

Expected: one approved execution atomically creates one investigation, one under-investigation risk annotation, one successful execution, and one audit event; readback occurs only through `ops_read`; explicit all-sellers/risk-status sensitivity evidence is present; no raw seller ID appears in public outputs.

- [ ] **Step 3: Verify idempotent recovery and scoped cleanup in the same test**

The test loses one post-commit response, reads the exact execution receipt, retries with the same identity, and proves all four committed object counts remain one. The reset adapter removes only the exact scenario rows before/after; another scenario sentinel remains unchanged.

- [ ] **Step 4: Verify zero application sessions and record exact sanitized counts**

Use the existing admin session-leak assertion and store only object counts, public refs/hashes, versions, test command, duration, and PASS/FAIL for the final report. Do not store credentials, raw business rows, raw seller ID, grant, nonce, signature, canonical payload, complete Trace payload, or private model state.

**Completion standard:** every negative case writes zero, one positive seller-risk transaction/readback passes on real PostgreSQL roles/functions/ACLs, response-loss recovery is idempotent, scenario reset is scoped, public evidence is sanitized, and session count is zero.

## Phase D: Final Verification and Reporting

### Task 18: Run the Non-Paid Day 4 Quality Gates and Write the Evidence Report

**Entry condition:** Offline Tasks 1-15 are complete. Gate D/W results may be PASS or explicitly NOT RUN, but the report and overall decision must preserve the exact state without upgrading missing evidence.

**Files:**
- Create: `docs/reports/2026-09-06-day-4-product-closed-loops.md`
- Modify only if a failing test exposes an approved Day 4 defect: the owning source/test from Tasks 1-15, followed by its local red/green/refactor cycle.

- [ ] **Step 1: Run Ruff and the full default offline suite**

Run:

```powershell
uv run ruff check src tests scripts db/migrations
uv run pytest -q --tb=line
```

Expected: PASS; PostgreSQL and DeepSeek markers are skipped by default; three deterministic Product loops pass using FakeModel/in-memory adapters; no network/database mutation occurs.

- [ ] **Step 2: If Gate D is active, run the full PostgreSQL suite explicitly**

Run only after Gate D authorization/activation:

```powershell
$env:COMMERCE_AGENT_RUN_POSTGRES_TESTS = '1'
$env:LANGGRAPH_STRICT_MSGPACK = 'true'
uv run --env-file .env pytest tests/integration -m postgres -q --tb=line
$savedExit = $LASTEXITCODE
Remove-Item Env:COMMERCE_AGENT_RUN_POSTGRES_TESTS
Remove-Item Env:LANGGRAPH_STRICT_MSGPACK
if ($savedExit -ne 0) { throw "Day 4 PostgreSQL integration failed with exit code $savedExit" }
```

Expected: all Product query, Knowledge, Resolver, checkpoint, model-state, operations, Trace, scenario, readback, ACL, transaction, privacy, and session-leak tests PASS. This command must not select the DeepSeek marker.

- [ ] **Step 3: Run explicit BIRD-isolation contract tests offline**

Run:

```powershell
uv run pytest tests/unit/orchestration/test_track_isolation.py tests/unit/orchestration/test_bird_a_graph.py tests/unit/orchestration/test_bird_c_responder.py tests/unit/orchestration/test_bird_tools.py -q
```

Expected: PASS; BIRD profile/tools/constructors/imports/fixtures remain Product-free, and no BIRD service/evaluation data is accessed.

- [ ] **Step 4: Scan only the explicit Day 4 source/config/test/report allowlist**

Build the file list from the paths in this plan's File Map plus the two named Day 4 fixture files and report. Do not scan repository root, `data/`, output caches, secret files, or any evaluator-only/evaluator_only location. Build sensitive patterns at runtime so the guard text does not match itself. Expected findings:

```text
no credential-like value or connection string
no raw seller identity value
no nonce/signature/HMAC key value
no complete provider request/response or private reasoning payload
no arbitrary write SQL, generic command batch, patch, handler, URL, or DSN field in public contracts
no final-closed reference answer in development/regression fixtures
all evaluator-only terms occur only in access-denial guard assertions/docs
```

- [ ] **Step 5: Write the report from exact command evidence only**

The report records:

```text
spec/design paths and SHA-256
implementation revision identifiers and config hashes
Ruff/default/PostgreSQL/BIRD-isolation exact commands, counts, duration, status
three deterministic scenario IDs and exact assertion summaries
negative authorization matrix with successful business writes = 0
proposal/decision/execution/nonce/business/audit state-transition evidence
SellerRef privacy evidence and explicit sensitivity comparison
backtest hit/exclusion/coverage evidence with historical-only wording
SqlReasoner generation/repair/no-progress and QueryEngine reconciliation evidence
role/object/function/view ACL matrix and safe-function properties
response-loss/idempotent recovery and zero-session evidence
Gate M/R/D/W/P/G exact status
omissions, limitations, and overall Day 4 decision
```

Overall Day 4 is PASS only if Gate W's real PostgreSQL seller-risk scenario passed plus every required offline/integration/ACL/privacy/BIRD-isolation test. If Gate D or W is not authorized/run, report `PARTIAL / REAL POSTGRESQL GATE NOT RUN`; deterministic fixtures cannot be represented as final PostgreSQL proof. The other two loops remain deterministic Product evidence until later real instances satisfy global final DoD.

- [ ] **Step 6: Verify report claims against artifacts and stop before paid/Git gates**

Re-run only failed owning tests after fixes, then the exact broader gate that caught the defect. Do not call a provider and do not stage/commit. Present the report and all gate statuses for review.

**Completion standard:** all authorized non-paid checks have exact results, the report separates fixture/PostgreSQL/not-run evidence, negative writes remain zero, limitations are explicit, and no paid/Git action is inferred.

## Independent Closed Gates

### Gate P: Paid Provider (Not Required for Day 4)

Day 4 architecture and acceptance require `FakeModel`, not a real DeepSeek or other paid request. The default and recommended Gate P disposition is `NOT REQUIRED / NOT RUN`.

- [ ] **P1: Verify default tests keep the paid marker skipped**

Run: `uv run pytest tests/integration/deepseek/test_retail_probe.py -q --tb=line`

Expected: SKIPPED with the explicit switch reason; zero provider request.

- [ ] **P2: Do not reuse the Day 3 v4 identity or artifact**

Any future paid validation requires a new user request that names a fresh run/attempt/probe/artifact identity, a precise maximum cost, refreshed reviewed capability/price evidence, and the exact scenario. That work receives its own plan amendment and authorization before the first request. No paid command belongs to this Day 4 implementation plan.

**Completion standard:** Gate P remains closed and clearly reported; absence of a paid call does not block Day 4 because the approved design explicitly makes it unnecessary.

### Gate G: Git Checkpoint Only After Separate Authorization

The current repository is unborn and mostly untracked. Do not infer scope from `git diff`. Review/stage only an explicit Day 4 allowlist after the user authorizes Git operations.

- [ ] **G1: Review explicit planned paths without enumerating unrelated or restricted content**

Use a PowerShell array containing each exact source/config/test/migration/script/report path listed in this plan. Run `git status --short -- $day4Paths` and inspect every returned path. Do not use a repository-wide untracked listing.

- [ ] **G2: Stage the exact reviewed allowlist**

```powershell
$day4Paths = @(
  'src/commerce_agent/operations/__init__.py',
  'src/commerce_agent/operations/contracts.py',
  'src/commerce_agent/operations/commands.py',
  'src/commerce_agent/operations/errors.py',
  'src/commerce_agent/operations/_canonical.py',
  'src/commerce_agent/operations/_approval.py',
  'src/commerce_agent/operations/_store.py',
  'src/commerce_agent/operations/_memory.py',
  'src/commerce_agent/operations/_seller_refs.py',
  'src/commerce_agent/operations/_backtest.py',
  'src/commerce_agent/operations/_postgres.py',
  'src/commerce_agent/operations/workflow.py',
  'src/commerce_agent/sql_reasoning/__init__.py',
  'src/commerce_agent/sql_reasoning/contracts.py',
  'src/commerce_agent/sql_reasoning/errors.py',
  'src/commerce_agent/sql_reasoning/reasoner.py',
  'src/commerce_agent/trace/__init__.py',
  'src/commerce_agent/trace/contracts.py',
  'src/commerce_agent/trace/errors.py',
  'src/commerce_agent/trace/module.py',
  'src/commerce_agent/trace/_memory.py',
  'src/commerce_agent/trace/_postgres.py',
  'src/commerce_agent/query_engine/contracts.py',
  'src/commerce_agent/query_engine/engine.py',
  'src/commerce_agent/query_engine/_ast_policy.py',
  'src/commerce_agent/query_engine/__init__.py',
  'src/commerce_agent/orchestration/contracts.py',
  'src/commerce_agent/orchestration/_checkpoint.py',
  'src/commerce_agent/orchestration/tools.py',
  'src/commerce_agent/orchestration/retail_graph.py',
  'src/commerce_agent/context_builder/contracts.py',
  'src/commerce_agent/context_builder/profiles.py',
  'src/commerce_agent/product_eval/__init__.py',
  'src/commerce_agent/product_eval/contracts.py',
  'src/commerce_agent/product_eval/driver.py',
  'src/commerce_agent/product_eval/_reset.py',
  'src/commerce_agent/config.py',
  'configs/model/run-profiles.v2.json',
  'configs/model/prompt-policies.v2.json',
  'tests/fixtures/product_eval/day4-development.v1.json',
  'tests/fixtures/product_eval/day4-regression.v1.json',
  'db/migrations/versions/0004_day4_product_operations.py',
  'scripts/provision_day4_operations_env.py',
  'scripts/bootstrap_day4_operations.py',
  '.env.example',
  'tests/support/__init__.py',
  'tests/support/product_eval_failures.py',
  'tests/unit/operations/__init__.py',
  'tests/unit/operations/test_contracts.py',
  'tests/unit/operations/test_commands.py',
  'tests/unit/operations/test_errors.py',
  'tests/unit/operations/test_canonical.py',
  'tests/unit/operations/test_approval.py',
  'tests/unit/operations/test_memory_store.py',
  'tests/unit/operations/test_workflow_propose.py',
  'tests/unit/operations/test_workflow_decide.py',
  'tests/unit/operations/test_workflow_execute.py',
  'tests/unit/operations/test_seller_refs.py',
  'tests/unit/operations/test_backtest.py',
  'tests/unit/operations/test_postgres_adapter.py',
  'tests/unit/sql_reasoning/__init__.py',
  'tests/unit/sql_reasoning/test_contracts.py',
  'tests/unit/sql_reasoning/test_reasoner.py',
  'tests/unit/sql_reasoning/test_fingerprints.py',
  'tests/unit/trace/__init__.py',
  'tests/unit/trace/test_contracts.py',
  'tests/unit/trace/test_module.py',
  'tests/unit/trace/test_memory.py',
  'tests/unit/trace/test_postgres_adapter.py',
  'tests/unit/query_engine/test_contracts.py',
  'tests/unit/query_engine/test_engine.py',
  'tests/unit/query_engine/test_reconciliation.py',
  'tests/unit/orchestration/test_contracts.py',
  'tests/unit/orchestration/test_tools.py',
  'tests/unit/orchestration/test_track_isolation.py',
  'tests/unit/orchestration/test_checkpoint.py',
  'tests/unit/orchestration/test_retail_graph.py',
  'tests/unit/orchestration/test_retail_day4_flow.py',
  'tests/unit/orchestration/test_retail_day4_privacy.py',
  'tests/unit/context_builder/test_contracts.py',
  'tests/unit/context_builder/test_profiles.py',
  'tests/unit/product_eval/__init__.py',
  'tests/unit/product_eval/test_contracts.py',
  'tests/unit/product_eval/test_driver.py',
  'tests/unit/product_eval/test_failure.py',
  'tests/unit/product_eval/test_privacy.py',
  'tests/unit/product_eval/test_day4_scenarios.py',
  'tests/unit/product_eval/test_day4_recovery.py',
  'tests/unit/product_eval/test_postgres_reset_adapter.py',
  'tests/unit/scripts/test_provision_day4_operations_env.py',
  'tests/unit/scripts/test_bootstrap_day4_operations.py',
  'tests/unit/test_day4_operations_migration.py',
  'tests/unit/test_config.py',
  'tests/integration/conftest.py',
  'tests/integration/operations/__init__.py',
  'tests/integration/operations/test_operation_workflow.py',
  'tests/integration/operations/test_operation_security.py',
  'tests/integration/operations/test_operation_transactions.py',
  'tests/integration/operations/test_operation_acls.py',
  'tests/integration/trace/__init__.py',
  'tests/integration/trace/test_product_trace.py',
  'tests/integration/product_eval/__init__.py',
  'tests/integration/product_eval/test_scenario_reset_security.py',
  'tests/integration/product_eval/test_seller_risk_postgres_scenario.py',
  'tests/integration/orchestration/test_retail_public_tools.py',
  'tests/integration/orchestration/test_ops_readback.py',
  'docs/reports/2026-09-06-day-4-product-closed-loops.md',
  'docs/superpowers/plans/2026-09-06-day-4-product-closed-loops.md'
)
git status --short -- $day4Paths
git add -- $day4Paths
```

Confirm every returned path belongs to this exact array. Never stage secret files, output artifacts containing private state, caches, data, or restricted evaluation content.

- [ ] **G3: Review staged content and create one Day 4 checkpoint**

Run only after explicit Git authorization:

```powershell
git diff --cached --check
git diff --cached --stat
git commit -m "feat: add day 4 product closed loops"
```

Expected: staged paths equal the reviewed allowlist, whitespace check passes, and the resulting commit ID is recorded in the report. Do not push without another explicit authorization.

**Completion standard:** exact allowlist only, no secret/data/private artifact, one reviewed commit ID, and no push.

## Execution Completion Checklist

- [ ] All eight operation commands and every public contract are immutable, discriminated, bounded, and fail closed.
- [ ] `OperationWorkflow` exposes only propose/decide/execute and hides canonicalization, signing, nonce, idempotency, transaction, and audit complexity.
- [ ] Proposal revision, first decision, self-approval denial, actor binding, 24-hour/10-minute expiry, payload/target binding, and one-time nonce invariants pass.
- [ ] Unauthorized, self-approved, tampered, expired, replayed, conflicted, and rolled-back cases produce zero successful business writes and zero success audits.
- [ ] Business state, successful execution, nonce consumption, and execution audit commit or roll back together.
- [ ] SellerRef is opaque/evidence-bound and raw seller identity is absent from all public artifacts; risk sensitivity comparison is explicit.
- [ ] Metric replay is deterministic/historical only; excluded windows do not hit; hit-to-task requires a second proposal and approval.
- [ ] SqlReasoner generation/repair/fingerprints/no-progress/budget and QueryEngine reconciliation pass with at most three repairs.
- [ ] Retail requires typed clarification/plan/report, uses a new attempt for each clarification reply, and cannot approve/execute or see grants.
- [ ] Retail state v2 rejects v1 checkpoints; resume does not duplicate model/query/proposal work; terminal cleanup retries finalize only.
- [ ] Three deterministic Product scenarios complete approval/execution/readback; failure injection proves idempotent recovery.
- [ ] Migration Source, Role Provisioning Source, Database Activation, Real PostgreSQL Write, Paid Provider, and Git Checkpoint gates retain independent status/authorization.
- [ ] PostgreSQL roles/ACLs/views/functions enforce least privilege, safe `SECURITY DEFINER`, append-only audit, scoped reset, and zero leaked application sessions.
- [ ] BIRD imports, tools, prompts, constructors, fixtures, state, credentials, and data remain Product-free.
- [ ] Ruff/default/PostgreSQL/isolation/privacy checks and the Day 4 report contain exact evidence and no overclaim.

## Plan Self-Review Matrix

| Requirement | Implementation coverage | Verification coverage |
|---|---|---|
| Global v0.3 precedence and Product/BIRD trust domains | Global Constraints; Tasks 7, 9, 10, 12 | Task 12 AST/profile/constructor checks; Task 18 isolation gate |
| Three-method deep `OperationWorkflow` | Tasks 1-4 | Unit contract/workflow/state suites |
| Single typed command and named composite action | Task 1 | Discriminator/unknown-field/command matrix tests |
| Immutable proposal versions and first-decision-wins | Task 4 | revision/idempotency/concurrency tests |
| Canonical JSON, HMAC, 24h/10m, one-time nonce | Tasks 2, 4 | golden vectors, tamper/expiry/replay tests |
| Business/audit atomicity and optimistic locking | Tasks 4, 13, 15 | memory rollback plus PostgreSQL transaction/concurrency tests |
| Least-privilege PostgreSQL roles/functions/views | Gates M/R/D; Task 15 | static migration, fake cursor, ACL/function-security integration |
| ops_read-only analysis readback | Tasks 8, 13, 15 | AST policy, view-column, raw-ops denial, readback tests |
| Opaque SellerRef and explicit risk sensitivity | Tasks 5, 9, 12 | tamper/cardinality/privacy and seller-risk scenario assertions |
| Historical metric alert replay and separate hit approval | Tasks 6, 12 | window/exclusion/hash tests and two-approval scenario |
| Typed clarification, InvestigationPlan/report evidence | Tasks 9-10 | hard-slot, missing-evidence, reconciliation, new-attempt tests |
| SqlReasoner and QueryEngine reconciliation | Tasks 7-8 | generation/repair/fingerprint/no-progress plus five rule families |
| Product Trace separated from execution audit | Tasks 3, 13, 15 | redaction/sequence/ACL tests and distinct report evidence |
| Deterministic ProductScenarioDriver and failures | Tasks 11-12 | scenario/validator/reset/import/recovery suites |
| Real PostgreSQL seller-risk proof | Gates D/W | negative zero-write matrix, one positive transaction/readback, sessions zero |
| Privacy and BIRD isolation | Global Constraints; Tasks 3, 5, 9-12, 18 | explicit allowlist scan and offline isolation tests |
| Paid provider not required | Gate P | paid marker stays skipped; no provider command in plan |
| Git authority independent | Gate G | explicit path review/staging only after authorization |

### Plan Author Self-Review Result

- **Specification consistency:** PASS. The plan preserves every global v0.3 Day 4 invariant and uses the approved Day 4 document only to refine ownership, interfaces, state machines, roles, and test evidence. No downstream rule overrides the global Product/BIRD, GMV, SQL, approval, audit, or evaluation boundary.
- **Dependency order:** PASS. Provider-neutral contracts/canonical/state/Trace precede workflow; SellerRef/backtest/SqlReasoner/reconciliation precede Retail; Retail precedes ProductScenarioDriver; deterministic scenarios precede PostgreSQL source; source precedes activation; activation precedes the real write loop.
- **Test completeness:** PASS. Unit, interface, state-machine, recovery, default-offline, PostgreSQL integration, ACL/function security, concurrency, privacy, session-leak, ProductScenarioDriver, and BIRD-isolation evidence each has an owning task and exact command.
- **Authorization boundary:** PASS. Migration source, role provisioning source, actual credential/role/migration state change, real Product write, paid provider, and Git checkpoint are six independent gates. No source task contains permission to execute its corresponding external effect.
- **Type consistency:** PASS. The same `ProposalRef`, `ExecutionGrant`, `ExecutionReceipt`, `SellerRef`, `AlertBacktestRef`, `InvestigationPlan`, `ProductDbError`, `ReconciliationResult`, and `ScopedTraceEvent` names flow from their owner to all downstream tasks.
- **Public-module boundary:** PASS. Operations does not import Retail/BIRD; Retail gets proposal-only capability; Product eval is downstream-only; QueryEngine owns reconciliation; Trace and execution audit remain distinct; no shallow per-command public adapter is introduced.
- **Mechanical plan review:** PASS after exact-file checks: task numbers are unique and ordered, named gates are distinct, checkbox steps are actionable, code fences balance, and no unresolved placeholder marker is present.

## Execution Handoff

This plan is complete only as a review artifact. Do not begin Task 1, create source files, run tests, change PostgreSQL, call a provider, or perform Git operations from this planning task. The next action is user review of this plan; execution mode is selected only after that review, and every later independent gate retains its own authorization requirement.
