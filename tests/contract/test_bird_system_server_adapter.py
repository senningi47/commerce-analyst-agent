"""Contract tests for BirdSystemServerAdapter (the official session harness).

Verifies the frozen-contract DTO field sets, the c-interact within-turn loop
(one submit per run_session, official state keys), the a-interact pass-through
with `BirdSessionStatePort` budget/trajectory semantics, and one integration
turn through the real `BirdCResponder`.
"""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from commerce_agent.context_builder._tokens import TokenEstimate
from commerce_agent.context_builder.builder import ContextBuilder, compute_config_hash, scope_digest
from commerce_agent.context_builder.profiles import ProfileRegistry
from commerce_agent.evaluation.contracts import load_official_contract
from commerce_agent.model._turn_store import AttemptRef
from commerce_agent.model.contracts import (
    CostUnavailable,
    FinalOutput,
    ModelAttemptSummary,
    ModelResponse,
    ProviderTurnRef,
    ReportedUsage,
    RunScope,
    ToolCall,
    ToolCallOutput,
    ToolResult,
    UsageUnavailable,
)
from commerce_agent.model.fake import FakeModel
from commerce_agent.orchestration.bird_c_responder import BirdCResponder
from commerce_agent.orchestration.bird_server import (
    BirdInitSessionRequest,
    BirdInitSessionResponse,
    BirdRunSessionRequest,
    BirdRunSessionResponse,
    BirdSessionStatePort,
    BirdSystemServerAdapter,
)
from commerce_agent.orchestration.contracts import (
    AskUserCandidate,
    BirdARunOutcome,
    BirdARunRequest,
    BirdCRequest,
    BirdCResponse,
    StopOutcome,
    SubmitSqlCandidate,
)

CONFIG_ROOT = Path(__file__).parents[2] / "configs" / "model"
KNOWN_ATTEMPT_ID = UUID("00000000-0000-0000-0000-000000000951")
NOW = datetime(2026, 9, 12, 2, 0, tzinfo=UTC)

_REGISTRY: ProfileRegistry | None = None


def registry() -> ProfileRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ProfileRegistry.load(CONFIG_ROOT)
    return _REGISTRY


class FixedEstimator:
    @property
    def revision(self) -> str:
        return "deepseek-tokenizer-v1"

    def estimate(self, request: object) -> TokenEstimate:
        del request
        return TokenEstimate(input_tokens=100, estimator_revision=self.revision)


class RecordingTurnStore:
    def __init__(self) -> None:
        self.deleted_attempts: list[AttemptRef] = []

    async def delete_attempt(self, attempt: AttemptRef) -> None:
        self.deleted_attempts.append(attempt)


class StubPort:
    def __init__(self, script: list[ToolResult]) -> None:
        self._script = script
        self.calls: list[ToolCall] = []

    async def execute(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        return self._script.pop(0)


class RepeatPort:
    def __init__(self, body: object) -> None:
        self._body = body
        self.calls: list[ToolCall] = []

    async def execute(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        return canned_result(call.name, self._body)


def canned_result(name: str, body: object, call_id: str = "stub") -> ToolResult:
    content_json = json.dumps(body, sort_keys=True, separators=(",", ":"))
    digest = sha256(content_json.encode("utf-8")).hexdigest()
    return ToolResult(
        call_id=call_id,
        name=name,
        status="success",
        content_json=content_json,
        deterministic_summary=json.dumps(
            {"content_sha256": digest, "source_refs": ("stub",)},
            sort_keys=True,
            separators=(",", ":"),
        ),
        content_sha256=digest,
        source_refs=("stub",),
    )


SUBMIT_BODY = {
    "passed": True,
    "message": "ok",
    "reward": 1.0,
    "phase_completed": 1,
    "has_follow_up": False,
    "follow_up_query": None,
}


def stub_c_response(candidate: AskUserCandidate | SubmitSqlCandidate) -> BirdCResponse:
    return BirdCResponse(
        candidate=candidate,
        usage=UsageUnavailable(status="unavailable", reason_code="stub"),
        cost=CostUnavailable(status="unavailable", reason_code="stub"),
        prompt_policy_hash="a" * 64,
        rendered_prompt_hash="b" * 64,
        tool_hash="c" * 64,
        context_hash="d" * 64,
        config_hash="e" * 64,
        attempt_id=uuid4(),
    )


class StubCHandler:
    def __init__(self, responses: list[BirdCResponse]) -> None:
        self._responses = responses
        self.requests: list[BirdCRequest] = []

    async def respond(self, request: BirdCRequest) -> BirdCResponse:
        self.requests.append(request)
        return self._responses.pop(0)


class EndlessAskHandler:
    async def respond(self, request: BirdCRequest) -> BirdCResponse:
        del request
        return stub_c_response(AskUserCandidate(type="ask_user", question="more?"))


def stub_a_outcome(content: str) -> BirdARunOutcome:
    return BirdARunOutcome(
        status="completed",
        final_output=FinalOutput(type="final", content=content),
        model_calls=1,
        tool_calls=0,
        prompt_policy_hash="a" * 64,
        rendered_prompt_hash="b" * 64,
        tool_hash="c" * 64,
        context_hash="d" * 64,
        config_hash="e" * 64,
        attempt_id=uuid4(),
    )


class StubAHandler:
    def __init__(self, outcomes: list[BirdARunOutcome]) -> None:
        self._outcomes = outcomes
        self.requests: list[BirdARunRequest] = []

    async def run(self, request: BirdARunRequest) -> BirdARunOutcome:
        self.requests.append(request)
        return self._outcomes.pop(0)


class HarnessFactory:
    def __init__(
        self,
        *,
        c_handler: StubCHandler | EndlessAskHandler | None = None,
        a_handler: StubAHandler | None = None,
        port: StubPort | RepeatPort | None = None,
    ) -> None:
        self._c_handler = c_handler
        self._a_handler = a_handler
        self._port = port

    def build_run_scope(self, *, mode: str, task_id: str) -> RunScope:
        profile = registry().get("bird_c" if mode == "c" else "bird_a")
        inference = next(rule.inference for rule in profile.inference_rules)
        return RunScope(
            run_id=uuid4(),
            track="bird",
            mode=mode,
            subject_id=task_id,
            experiment_id="day5",
            config_hash=compute_config_hash(profile, inference, "deepseek-chat"),
        )

    def build_tool_port(self, *, task_id: str) -> StubPort | RepeatPort:
        del task_id
        assert self._port is not None, "harness factory requires a port"
        return self._port

    def build_c(self, *, run_scope: RunScope) -> StubCHandler | EndlessAskHandler:
        del run_scope
        assert self._c_handler is not None, "harness factory requires a c handler"
        return self._c_handler

    def build_a(self, **kwargs: object) -> StubAHandler:
        self.build_a_kwargs = kwargs
        assert self._a_handler is not None, "harness factory requires an a handler"
        return self._a_handler


def make_adapter(
    *, c_handler=None, a_handler=None, port=None
) -> BirdSystemServerAdapter:
    return BirdSystemServerAdapter(
        factory=HarnessFactory(c_handler=c_handler, a_handler=a_handler, port=port),
        attempt_id_factory=lambda: KNOWN_ATTEMPT_ID,
    )


def init_request(
    mode: str = "c-interact", state: dict[str, object] | None = None
) -> BirdInitSessionRequest:
    return BirdInitSessionRequest(task_id="task-1", mode=mode, state=state, reset=False)


def test_adapter_dtos_match_frozen_contract() -> None:
    contract = load_official_contract()
    assert set(BirdInitSessionRequest.model_fields) == contract.init_session_request_fields
    assert set(BirdInitSessionResponse.model_fields) == contract.init_session_response_fields
    assert set(BirdRunSessionRequest.model_fields) == contract.run_session_request_fields
    assert set(BirdRunSessionResponse.model_fields) == contract.run_session_response_fields


def test_init_session_creates_and_honors_reset() -> None:
    adapter = make_adapter(port=RepeatPort({"answer": "x"}), c_handler=EndlessAskHandler())
    first = adapter.init_session(init_request())
    again = adapter.init_session(init_request())
    assert first.adk_available is True
    assert first.session_id == again.session_id

    fresh = adapter.init_session(
        BirdInitSessionRequest(task_id="task-1", mode="c-interact", state={}, reset=True)
    )
    assert fresh.session_id != first.session_id


def test_run_session_auto_initializes_missing_session() -> None:
    adapter = make_adapter(port=RepeatPort({"answer": "x"}), c_handler=EndlessAskHandler())
    response = asyncio.run(
        adapter.run_session(
            BirdRunSessionRequest(task_id="task-9", mode="c-interact", message="hi")
        )
    )
    assert response.task_id == "task-9"
    assert response.adk_available is True
    assert response.state["model_turns"] == 60  # auto-init then one full c loop


def test_c_run_clarify_then_submit_updates_state() -> None:
    handler = StubCHandler(
        [
            stub_c_response(AskUserCandidate(type="ask_user", question="Which year?")),
            stub_c_response(SubmitSqlCandidate(type="submit_sql", sql="SELECT 1")),
        ]
    )
    port = StubPort(
        [
            canned_result("ask_user", {"answer": "2018"}),
            canned_result("submit_sql", SUBMIT_BODY),
        ]
    )
    adapter = make_adapter(c_handler=handler, port=port)

    response = asyncio.run(
        adapter.run_session(
            BirdRunSessionRequest(task_id="task-1", mode="c-interact", message="analyze")
        )
    )

    assert response.response == "SQL submitted. Awaiting result."
    state = response.state
    assert state["dialogue_history"] == [
        {"role": "agent", "content": "Which year?"},
        {"role": "user", "content": "2018"},
    ]
    assert state["phase1_completed"] is True
    assert state["task_done"] is True
    assert state["total_reward"] == 1.0
    assert state["_last_submit_raw"] == "ok"
    assert state["_submitted_this_phase"] is True
    assert state["model_turns"] == 2
    assert [call.name for call in port.calls] == ["ask_user", "submit_sql"]
    submit_args = json.loads(port.calls[1].arguments_json)
    assert submit_args == {"sql": "SELECT 1"}


def test_c_run_resets_submitted_flag_next_call() -> None:
    handler = StubCHandler(
        [
            stub_c_response(AskUserCandidate(type="ask_user", question="q?")),
            stub_c_response(SubmitSqlCandidate(type="submit_sql", sql="SELECT 1")),
            stub_c_response(SubmitSqlCandidate(type="submit_sql", sql="SELECT 2")),
        ]
    )
    port = StubPort(
        [
            canned_result("ask_user", {"answer": "a"}),
            canned_result("submit_sql", SUBMIT_BODY),
            canned_result("submit_sql", SUBMIT_BODY),
        ]
    )
    adapter = make_adapter(c_handler=handler, port=port)
    run = BirdRunSessionRequest(task_id="task-1", mode="c-interact", message="m")

    asyncio.run(adapter.run_session(run))
    second = asyncio.run(adapter.run_session(run))

    assert second.response == "SQL submitted. Awaiting result."
    assert second.state["model_turns"] == 3
    assert second.state["_submitted_this_phase"] is True


def test_c_run_stops_at_max_model_turns() -> None:
    adapter = make_adapter(port=RepeatPort({"answer": "x"}), c_handler=EndlessAskHandler())

    response = asyncio.run(
        adapter.run_session(
            BirdRunSessionRequest(task_id="task-1", mode="c-interact", message="go")
        )
    )

    assert response.response == "Maximum turns reached. Task ended."
    assert response.state["model_turns"] == 60


class RecordingAskHandler:
    """Endless ask_user that records every request's phase datum."""

    def __init__(self) -> None:
        self.requests: list[BirdCRequest] = []

    async def respond(self, request: BirdCRequest) -> BirdCResponse:
        self.requests.append(request)
        return stub_c_response(AskUserCandidate(type="ask_user", question="more?"))


def test_c_clarification_budget_gate_blocks_ask_user_after_max_turn() -> None:
    port = RepeatPort({"answer": "x"})
    adapter = make_adapter(port=port, c_handler=RecordingAskHandler())
    adapter.init_session(init_request(state={"max_turn": 2}))

    response = asyncio.run(
        adapter.run_session(
            BirdRunSessionRequest(task_id="task-1", mode="c-interact", message="go")
        )
    )

    assert response.response == "Maximum turns reached. Task ended."
    executed_asks = [call for call in port.calls if call.name == "ask_user"]
    assert len(executed_asks) == 2
    assert response.state["_ask_user_turns"] == 2
    assert response.state["model_turns"] == 60


def test_c_clarification_gate_injects_live_budget_into_phase_record() -> None:
    handler = RecordingAskHandler()
    adapter = make_adapter(port=RepeatPort({"answer": "x"}), c_handler=handler)
    adapter.init_session(init_request(state={"max_turn": 2}))

    asyncio.run(
        adapter.run_session(
            BirdRunSessionRequest(task_id="task-1", mode="c-interact", message="go")
        )
    )

    first = handler.requests[0].current_phase.content
    assert "[clarification budget: 0 of 2 ask_user turns used" in first
    third = handler.requests[2].current_phase.content
    assert "[clarification budget: 2 of 2 ask_user turns used" in third
    fourth = handler.requests[3].current_phase.content
    assert "Clarification budget exhausted (2 of 2 ask_user turns used)" in fourth
    assert "[clarification budget: 2 of 2 ask_user turns used" in fourth


def test_c_clarification_budget_gated_ask_reaches_submit_within_cap() -> None:
    handler = StubCHandler(
        [
            stub_c_response(AskUserCandidate(type="ask_user", question="q1")),
            stub_c_response(AskUserCandidate(type="ask_user", question="q2")),
            stub_c_response(AskUserCandidate(type="ask_user", question="q3")),
            stub_c_response(SubmitSqlCandidate(type="submit_sql", sql="SELECT 1")),
        ]
    )
    port = StubPort(
        [
            canned_result("ask_user", {"answer": "a1"}),
            canned_result("ask_user", {"answer": "a2"}),
            canned_result("submit_sql", SUBMIT_BODY),
        ]
    )
    adapter = make_adapter(c_handler=handler, port=port)
    adapter.init_session(init_request(state={"max_turn": 2}))

    response = asyncio.run(
        adapter.run_session(
            BirdRunSessionRequest(task_id="task-1", mode="c-interact", message="go")
        )
    )

    assert response.response == "SQL submitted. Awaiting result."
    assert response.state["_ask_user_turns"] == 2
    assert response.state["model_turns"] == 4
    assert response.state["dialogue_history"] == [
        {"role": "agent", "content": "q1"},
        {"role": "user", "content": "a1"},
        {"role": "agent", "content": "q2"},
        {"role": "user", "content": "a2"},
    ]
    assert len(port.calls) == 3


def test_c_clarification_budget_resets_per_run_session() -> None:
    handler = StubCHandler(
        [
            stub_c_response(AskUserCandidate(type="ask_user", question="q1")),
            stub_c_response(SubmitSqlCandidate(type="submit_sql", sql="SELECT 1")),
            stub_c_response(AskUserCandidate(type="ask_user", question="q2")),
            stub_c_response(SubmitSqlCandidate(type="submit_sql", sql="SELECT 2")),
        ]
    )
    port = StubPort(
        [
            canned_result("ask_user", {"answer": "a1"}),
            canned_result("submit_sql", SUBMIT_BODY),
            canned_result("ask_user", {"answer": "a2"}),
            canned_result("submit_sql", SUBMIT_BODY),
        ]
    )
    adapter = make_adapter(c_handler=handler, port=port)
    adapter.init_session(init_request(state={"max_turn": 1}))
    run = BirdRunSessionRequest(task_id="task-1", mode="c-interact", message="m")

    first = asyncio.run(adapter.run_session(run))
    second = asyncio.run(adapter.run_session(run))

    assert first.state["_ask_user_turns"] == 1
    assert second.state["_ask_user_turns"] == 1
    assert second.response == "SQL submitted. Awaiting result."


def test_a_run_completed_passes_through() -> None:
    handler = StubAHandler([stub_a_outcome("SELECT 1")])
    adapter = make_adapter(a_handler=handler, port=StubPort([]))

    response = asyncio.run(
        adapter.run_session(
            BirdRunSessionRequest(task_id="task-1", mode="a-interact", message="solve")
        )
    )

    assert response.response == "SELECT 1"
    request = handler.requests[0]
    assert request.run_scope.mode == "a"
    assert request.attempt_id == KNOWN_ATTEMPT_ID
    assert request.current_input.content == "solve"


def test_a_run_stopped_max_turns_text() -> None:
    outcome = BirdARunOutcome(
        status="stopped",
        stop=StopOutcome(kind="budget_exhausted", reason_code="model_call_limit", retryable=False),
        model_calls=6,
        tool_calls=0,
        prompt_policy_hash="a" * 64,
        rendered_prompt_hash="b" * 64,
        tool_hash="c" * 64,
        context_hash="d" * 64,
        config_hash="e" * 64,
        attempt_id=uuid4(),
    )
    adapter = make_adapter(a_handler=StubAHandler([outcome]), port=StubPort([]))

    response = asyncio.run(
        adapter.run_session(
            BirdRunSessionRequest(task_id="task-1", mode="a-interact", message="solve")
        )
    )

    assert response.response == "Maximum interaction turns reached. Task ended."


def test_a_run_uses_official_turn_budget_and_forwards_gate() -> None:
    handler = StubAHandler([stub_a_outcome("SELECT 1")])
    factory = HarnessFactory(a_handler=handler, port=StubPort([]))
    adapter = BirdSystemServerAdapter(
        factory=factory, attempt_id_factory=lambda: KNOWN_ATTEMPT_ID
    )

    asyncio.run(
        adapter.run_session(
            BirdRunSessionRequest(task_id="task-1", mode="a-interact", message="solve")
        )
    )

    request = handler.requests[0]
    assert request.max_model_calls == 60
    assert request.max_tool_calls == 60
    assert factory.build_a_kwargs["model_turn_gate"] is not None


def test_a_run_maps_task_done_gate_stop_to_official_text() -> None:
    outcome = BirdARunOutcome(
        status="stopped",
        stop=StopOutcome(kind="completed", reason_code="official_task_done", retryable=False),
        model_calls=3,
        tool_calls=2,
        prompt_policy_hash="a" * 64,
        rendered_prompt_hash="b" * 64,
        tool_hash="c" * 64,
        context_hash="d" * 64,
        config_hash="e" * 64,
        attempt_id=uuid4(),
    )
    adapter = make_adapter(a_handler=StubAHandler([outcome]), port=StubPort([]))

    response = asyncio.run(
        adapter.run_session(
            BirdRunSessionRequest(task_id="task-1", mode="a-interact", message="solve")
        )
    )

    assert response.response == "Task completed."


def test_a_run_maps_budget_signal_stop_to_official_text() -> None:
    outcome = BirdARunOutcome(
        status="stopped",
        stop=StopOutcome(kind="budget_exhausted", reason_code="coin_budget_signal", retryable=False),
        model_calls=4,
        tool_calls=3,
        prompt_policy_hash="a" * 64,
        rendered_prompt_hash="b" * 64,
        tool_hash="c" * 64,
        context_hash="d" * 64,
        config_hash="e" * 64,
        attempt_id=uuid4(),
    )
    adapter = make_adapter(a_handler=StubAHandler([outcome]), port=StubPort([]))

    response = asyncio.run(
        adapter.run_session(
            BirdRunSessionRequest(task_id="task-1", mode="a-interact", message="solve")
        )
    )

    assert response.response == "Budget exhausted. Task ended."


def test_budget_gate_blocks_non_submit_when_short() -> None:
    state: dict[str, object] = {"budget_remaining": 0.5}
    inner = StubPort([])
    port = BirdSessionStatePort(
        inner=inner, state=state, mode="a", costs={"get_schema": Decimal(1)}
    )

    result = asyncio.run(port.execute(_call("get_schema", {})))

    assert inner.calls == []
    assert "Budget exhausted (0.5 remaining)" in result.content_json
    assert state["budget_remaining"] == 0.5
    # the rejection rides the model's own tool-call identity: the closed
    # ToolExchangeGroup on the next turn requires matching ids and names
    assert result.call_id == "call_1"
    assert result.name == "get_schema"


def test_budget_gate_submit_free_exit_and_state_update() -> None:
    state: dict[str, object] = {"budget_remaining": 2.0}
    inner = StubPort([canned_result("submit_sql", SUBMIT_BODY)])
    port = BirdSessionStatePort(
        inner=inner, state=state, mode="a", costs={"submit_sql": Decimal(3)}
    )

    result = asyncio.run(port.execute(_call("submit_sql", {"sql": "SELECT 1"})))

    assert inner.calls != []
    assert state["budget_remaining"] == -1
    assert state["phase1_completed"] is True
    assert state["total_reward"] == 1.0
    assert state["_last_submit_raw"] == "ok"
    assert state["task_done"] is True
    assert len(state["tool_trajectory"]) == 1  # type: ignore[arg-type]
    assert result.status == "success"


def test_ask_user_records_history_without_submit_flag() -> None:
    state: dict[str, object] = {}
    inner = StubPort([canned_result("ask_user", {"answer": "2017"})])
    port = BirdSessionStatePort(inner=inner, state=state, mode="c", costs={})

    asyncio.run(port.execute(_call("ask_user", {"question": "year?"})))

    assert state["dialogue_history"] == [
        {"role": "agent", "content": "year?"},
        {"role": "user", "content": "2017"},
    ]
    assert "_submitted_this_phase" not in state


def test_unknown_fields_and_modes_are_rejected() -> None:
    with pytest.raises(ValidationError):
        BirdInitSessionRequest.model_validate(
            {"task_id": "t", "mode": "c-interact", "bogus": 1}
        )
    with pytest.raises(ValidationError):
        BirdRunSessionRequest.model_validate(
            {"task_id": "t", "mode": "b-interact", "message": "m"}
        )


def test_real_c_responder_submit_turn_integration() -> None:
    port = StubPort([canned_result("submit_sql", SUBMIT_BODY)])
    factory = _RealCSubmitFactory(port=port)
    adapter = BirdSystemServerAdapter(
        factory=factory, attempt_id_factory=lambda: KNOWN_ATTEMPT_ID
    )
    session = adapter.init_session(init_request())

    response = asyncio.run(
        adapter.run_session(
            BirdRunSessionRequest(task_id="task-1", mode="c-interact", message="phase 1")
        )
    )

    assert session.session_id == response.session_id
    assert response.response == "SQL submitted. Awaiting result."
    assert response.state["phase1_completed"] is True
    assert response.state["model_turns"] == 1


def _call(name: str, arguments: dict[str, object]) -> ToolCall:
    return ToolCall(
        call_id="call_1",
        name=name,
        arguments_json=json.dumps(arguments, sort_keys=True, separators=(",", ":")),
    )


class _RealCSubmitFactory(HarnessFactory):
    """Builds the real BirdCResponder with a FakeModel bound to the session scope."""

    def build_c(self, *, run_scope: RunScope) -> object:
        submit_call = ToolCall(
            call_id="c_call",
            name="submit_sql",
            arguments_json=json.dumps({"sql": "SELECT 1"}, sort_keys=True, separators=(",", ":")),
        )
        gateway = FakeModel([_c_model_response(run_scope, (submit_call,))])
        return BirdCResponder(
            context_builder=ContextBuilder(
                registry=registry(),
                estimator=FixedEstimator(),
                requested_model="deepseek-chat",
                timeout_seconds=Decimal(30),
                provider_user_id="c" * 32,
            ),
            profile=registry().get("bird_c"),
            gateway=gateway,
            turn_store=RecordingTurnStore(),
        )


def _c_model_response(scope: RunScope, calls: tuple[ToolCall, ...]) -> ModelResponse:
    usage = ReportedUsage(
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
        usage=usage,
        cost=cost,
    )
    return ModelResponse(
        output=ToolCallOutput(type="tool_calls", tool_calls=calls),
        provider_turn_ref=ProviderTurnRef(
            turn_id=UUID(int=1),
            scope_digest=scope_digest(scope),
            attempt_id=KNOWN_ATTEMPT_ID,
            sequence=0,
            payload_sha256="1" * 64,
            expected_tool_call_ids=tuple(call.call_id for call in calls),
            token_weight=2,
            expires_at=NOW,
        ),
        finish_reason="tool_calls",
        actual_model="fake",
        system_fingerprint="fake-v1",
        usage=usage,
        cost=cost,
        attempts=(attempt,),
    )
