"""BirdSystemServerAdapter: the official system-agent session contract over BirdA/BirdC.

Implements the frozen inbound contract (`/init_session`, `/run_session` field
sets come from `load_official_contract()`) as an in-memory session harness:

- session state keys mirror the official ADK session exactly where the
  orchestrator reads them (phase flags, `budget_remaining`, `_last_submit_raw`,
  `dialogue_history`, `tool_trajectory`);
- c-interact runs the official within-turn loop: one submit per `run_session`
  (`_submitted_this_phase` is reset at entry, as `adk_runtime` does), bounded
  by the official `MAX_MODEL_TURNS = 60`; the official clarification budget
  (state `max_turn`) is enforced as a result-replacing ask_user gate and a
  live budget line in each turn's phase record (Task 5 policy layer);
- a-interact runs one `BirdAGraph` attempt per `run_session`; official budget
  gating and trajectory bookkeeping live in `BirdSessionStatePort`, a
  `BirdToolPort` decorator, so the graph itself stays coin-agnostic;
- phase control stays with the official orchestrator (v0.3 §6.2): the adapter
  never re-loops across `run_session` calls.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from decimal import Decimal
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from commerce_agent.context_builder.contracts import ContextDatum
from commerce_agent.evaluation.contracts import load_official_contract
from commerce_agent.model.contracts import RunScope, ToolCall, ToolResult
from commerce_agent.orchestration.bird_a_graph import BirdAModelTurnGate
from commerce_agent.orchestration.contracts import (
    BirdARunOutcome,
    BirdARunRequest,
    BirdCRequest,
    BirdCResponse,
    StopKind,
    StopOutcome,
)
from commerce_agent.orchestration.tools import BirdToolPort

_MAX_MODEL_TURNS = 60  # official callbacks.py / callbacks_cinteract.py MAX_MODEL_TURNS
_C_SUBMIT_FORCED_RESPONSE = "SQL submitted. Awaiting result."
_C_MAX_TURNS_TEXT = "Maximum turns reached. Task ended."
_A_MAX_TURNS_TEXT = "Maximum interaction turns reached. Task ended."
_A_TASK_DONE_TEXT = "Task completed."  # official before_model_callback task_done branch
_A_BUDGET_EXHAUSTED_TEXT = "Budget exhausted. Task ended."  # official budget<0 branch

_INTERNAL_MODE: dict[str, Literal["a", "c"]] = {
    "a-interact": "a",
    "c-interact": "c",
}


def _clarification_budget(state: Mapping[str, object]) -> int | None:
    """Official c-interact clarification cap (session state key `max_turn`).

    The orchestrator seeds it per task (ambiguities + patience). Absent or
    non-positive means the cap is unknown and the gate stays disabled,
    matching the prompt-only pre-Task-5 behavior.
    """
    raw = state.get("max_turn")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None
    return raw


def _phase_content(feedback: str, state: Mapping[str, object]) -> str:
    """Feedback plus the live clarification budget line (Task 5 policy layer)."""
    max_turn = _clarification_budget(state)
    if max_turn is None:
        return feedback
    used = int(state.get("_ask_user_turns", 0))
    return (
        f"{feedback}\n\n[clarification budget: {used} of {max_turn} ask_user turns "
        "used; once exhausted you must call submit_sql]"
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _preview(value: object, limit: int = 2000) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return text[:limit] + "...<truncated>" if len(text) > limit else text


class BirdInitSessionRequest(BaseModel, frozen=True, extra="forbid"):
    task_id: str = Field(min_length=1)
    mode: Literal["a-interact", "c-interact"]
    state: Mapping[str, object] | None = None
    reset: bool = False


class BirdInitSessionResponse(BaseModel, frozen=True, extra="forbid"):
    task_id: str
    mode: Literal["a-interact", "c-interact"]
    session_id: str
    adk_available: bool


class BirdRunSessionRequest(BaseModel, frozen=True, extra="forbid"):
    task_id: str = Field(min_length=1)
    mode: Literal["a-interact", "c-interact"]
    message: str = Field(min_length=1)


class BirdRunSessionResponse(BaseModel, frozen=True, extra="forbid"):
    task_id: str
    mode: Literal["a-interact", "c-interact"]
    session_id: str
    response: str
    state: Mapping[str, object]
    adk_available: bool


class BirdCHandler(Protocol):
    async def respond(self, request: BirdCRequest) -> BirdCResponse: ...


class BirdAHandler(Protocol):
    async def run(self, request: BirdARunRequest) -> BirdARunOutcome: ...


class BirdRuntimeFactory(Protocol):
    """Per-session runtime construction; production wires DeepSeek + HTTP port."""

    def build_run_scope(
        self, *, mode: Literal["a", "c"], task_id: str
    ) -> RunScope: ...

    def build_tool_port(self, *, task_id: str) -> BirdToolPort: ...

    def build_c(self, *, run_scope: RunScope) -> BirdCHandler: ...

    def build_a(
        self,
        *,
        run_scope: RunScope,
        attempt_id: UUID,
        tool_port: BirdToolPort,
        max_model_calls: int,
        max_tool_calls: int,
        model_turn_gate: BirdAModelTurnGate | None = None,
    ) -> BirdAHandler: ...


class BirdSessionStatePort:
    """Official tool semantics as a `BirdToolPort` decorator.

    Replicates the official tool/callback split on top of the transport port:
    a-mode budget gating (`before_tool_callback`), trajectory recording
    (`after_tool_callback`), the ask_user dialogue history (official
    `ask_user` tool), and the submit_sql state updates (official `submit_sql`
    tool). The wrapped graph or responder loop stays coin-agnostic.
    """

    def __init__(
        self,
        *,
        inner: BirdToolPort,
        state: dict[str, object],
        mode: Literal["a", "c"],
        costs: Mapping[str, Decimal],
    ) -> None:
        self._inner = inner
        self._state = state
        self._mode = mode
        self._costs = dict(costs)

    async def execute(self, call: ToolCall) -> ToolResult:
        if self._mode == "a":
            gated = self._gate_budget(call)
            if gated is not None:
                return gated
        elif self._mode == "c" and call.name == "ask_user":
            gated = self._gate_clarification_budget(call)
            if gated is not None:
                return gated
        result = await self._inner.execute(call)
        self._after_tool(call, result)
        return result

    def _gate_budget(self, call: ToolCall) -> ToolResult | None:
        name = call.name
        cost = self._costs.get(name)
        if cost is None:
            return None
        budget = float(self._state.get("budget_remaining", 0))
        self._state["_budget_before"] = budget
        if budget < cost:
            if name == "submit_sql":
                self._state["budget_remaining"] = -1  # official free exit
                return None
            text = (
                f"Budget exhausted ({budget:.1f} remaining). "
                "You MUST call submit_sql now with your best SQL."
            )
            # the rejection rides the model's own tool-call identity: the next
            # turn's ToolExchangeGroup requires matching ids and names
            return self._text_result(call, text)
        remaining = budget - float(cost)
        if name == "submit_sql" and remaining <= 0:
            remaining = -1  # official stop signal
        self._state["budget_remaining"] = remaining
        return None

    def _after_tool(self, call: ToolCall, result: ToolResult) -> None:
        name = call.name
        try:
            arguments = json.loads(call.arguments_json)
        except json.JSONDecodeError:
            arguments = {}
        trajectory = list(self._state.get("tool_trajectory", []))  # type: ignore[arg-type]
        trajectory.append(
            {
                "type": "tool",
                "tool": name,
                "args": arguments,
                "result": _preview(result.content_json),
            }
        )
        self._state["tool_trajectory"] = trajectory
        if name == "ask_user" and result.status == "success":
            if self._mode == "c":
                self._state["_ask_user_turns"] = (
                    int(self._state.get("_ask_user_turns", 0)) + 1
                )
            try:
                answer = str(json.loads(result.content_json).get("answer", ""))
            except json.JSONDecodeError:
                answer = ""
            history = list(self._state.get("dialogue_history", []))  # type: ignore[arg-type]
            history.append({"role": "agent", "content": arguments.get("question", "")})
            history.append({"role": "user", "content": answer})
            self._state["dialogue_history"] = history
        if name == "submit_sql":
            self._apply_submit_state(result)
            if self._mode == "c":
                self._state["_submitted_this_phase"] = True

    def _apply_submit_state(self, result: ToolResult) -> None:
        if result.status != "success":
            return
        try:
            body = json.loads(result.content_json)
        except json.JSONDecodeError:
            return
        if not isinstance(body, dict):
            return
        if body.get("passed") is True:
            reward = float(body.get("reward", 0.0))
            self._state["total_reward"] = float(self._state.get("total_reward", 0.0)) + reward
            phase = body.get("phase_completed")
            if phase == 1:
                self._state["phase1_completed"] = True
                self._state["current_phase"] = 2
                if body.get("has_follow_up") is not True:
                    self._state["task_done"] = True
            elif phase == 2:
                self._state["phase2_completed"] = True
                self._state["task_done"] = True
        self._state["_last_submit_raw"] = body.get("message", "")

    def _gate_clarification_budget(self, call: ToolCall) -> ToolResult | None:
        max_turn = _clarification_budget(self._state)
        if max_turn is None:
            return None
        used = int(self._state.get("_ask_user_turns", 0))
        if used < max_turn:
            return None
        text = (
            f"Clarification budget exhausted ({used} of {max_turn} ask_user turns "
            "used). You MUST call submit_sql with your best query now."
        )
        # the reminder rides the model's own tool-call identity and flows back
        # through the ask_user answer channel (see _run_c feedback extraction)
        return self._text_result(
            call, text, key="answer", source_ref="bird:clarification_gate"
        )

    def _text_result(
        self,
        call: ToolCall,
        text: str,
        *,
        key: str = "text",
        source_ref: str = "bird:budget_gate",
    ) -> ToolResult:
        content_json = _canonical_json({key: text})
        digest = sha256(content_json.encode("utf-8")).hexdigest()
        return ToolResult(
            call_id=call.call_id,
            name=call.name,
            status="success",
            content_json=content_json,
            deterministic_summary=_canonical_json(
                {"content_sha256": digest, "source_refs": (source_ref,)}
            ),
            content_sha256=digest,
            source_refs=(source_ref,),
        )


class _BudgetStopGate:
    """Official a-mode `before_model_callback` over shared session state.

    Replicates the official gate ordering: `task_done` first, then the
    exhausted-budget stop signal (`budget_remaining < 0`, set by the free
    submit exit). Coin semantics stay here; the graph only relays the
    resulting stop primitives.
    """

    def __init__(self, state: dict[str, object]) -> None:
        self._state = state

    async def stop_model_turn(self) -> StopOutcome | None:
        if self._state.get("task_done") is True:
            return StopOutcome(
                kind=StopKind.COMPLETED,
                reason_code="official_task_done",
                retryable=False,
            )
        budget = self._state.get("budget_remaining")
        if isinstance(budget, (int, float)) and budget < 0:
            return StopOutcome(
                kind=StopKind.BUDGET_EXHAUSTED,
                reason_code="coin_budget_signal",
                retryable=False,
            )
        return None


def _phase_datum(source_ref: str, content: str) -> ContextDatum:
    return ContextDatum(
        kind="phase",
        namespace="bird_c_phase",
        source_ref=source_ref,
        revision=None,
        content=content,
        digest=sha256(content.encode("utf-8")).hexdigest(),
    )


def _user_datum(source_ref: str, content: str) -> ContextDatum:
    return ContextDatum(
        kind="user_input",
        namespace="user_input",
        source_ref=source_ref,
        revision=None,
        content=content,
        digest=sha256(content.encode("utf-8")).hexdigest(),
    )


class _Session:
    """One official session: owned state plus mode-specific turn handling."""

    def __init__(
        self,
        *,
        factory: BirdRuntimeFactory,
        mode: Literal["a", "c"],
        task_id: str,
        state: dict[str, object],
        attempt_ids: Callable[[], UUID],
    ) -> None:
        self.mode = mode
        self.task_id = task_id
        self.session_id = str(uuid4())
        self.state = state
        self.model_turns = int(state.get("model_turns", 0))
        self._factory = factory
        self._attempt_ids = attempt_ids
        self._run_scope = factory.build_run_scope(mode=mode, task_id=task_id)
        contract = load_official_contract()
        self._costs = {action.name: action.coin_cost for action in contract.actions}
        inner_port = factory.build_tool_port(task_id=task_id)
        self._port = BirdSessionStatePort(
            inner=inner_port, state=self.state, mode=mode, costs=self._costs
        )
        self._c_handler: BirdCHandler | None = (
            factory.build_c(run_scope=self._run_scope) if mode == "c" else None
        )

    async def run(self, message: str) -> str:
        if self.mode == "c":
            return await self._run_c(message)
        return await self._run_a(message)

    async def _run_c(self, message: str) -> str:
        self.state["_submitted_this_phase"] = False  # official pre-run reset
        self.state["_ask_user_turns"] = 0  # per-phase clarification budget reset
        handler = self._c_handler
        if handler is None:  # pragma: no cover - constructor guarantees presence
            raise RuntimeError("c handler missing")
        feedback = message
        while self.model_turns < _MAX_MODEL_TURNS:
            self.model_turns += 1
            self.state["model_turns"] = self.model_turns
            request_turn = self.model_turns
            response = await handler.respond(
                BirdCRequest(
                    run_scope=self._run_scope,
                    attempt_id=self._attempt_ids(),
                    current_phase=_phase_datum(
                        f"bird-session:{self.session_id}:turn{request_turn}",
                        _phase_content(feedback, self.state),
                    ),
                )
            )
            candidate = response.candidate
            if candidate.type == "ask_user":
                result = await self._port.execute(
                    ToolCall(
                        call_id=f"c_ask_{request_turn}",
                        name="ask_user",
                        arguments_json=_canonical_json({"question": candidate.question}),
                    )
                )
                feedback = _answer_text(result)
                continue
            await self._port.execute(
                ToolCall(
                    call_id=f"c_submit_{request_turn}",
                    name="submit_sql",
                    arguments_json=_canonical_json({"sql": candidate.sql}),
                )
            )
            return _C_SUBMIT_FORCED_RESPONSE
        self.state["model_turns"] = self.model_turns
        return _C_MAX_TURNS_TEXT

    async def _run_a(self, message: str) -> str:
        attempt_id = self._attempt_ids()
        request = BirdARunRequest(
            run_scope=self._run_scope,
            attempt_id=attempt_id,
            current_input=_user_datum(
                f"bird-session:{self.session_id}:turn", message
            ),
            max_model_calls=_MAX_MODEL_TURNS,
            max_tool_calls=_MAX_MODEL_TURNS,
        )
        handler = self._factory.build_a(
            run_scope=self._run_scope,
            attempt_id=attempt_id,
            tool_port=self._port,
            max_model_calls=request.max_model_calls,
            max_tool_calls=request.max_tool_calls,
            model_turn_gate=_BudgetStopGate(self.state),
        )
        outcome = await handler.run(request)
        if outcome.status == "completed" and outcome.final_output is not None:
            return outcome.final_output.content
        if outcome.stop is not None:
            if outcome.stop.kind == StopKind.COMPLETED and outcome.stop.reason_code == (
                "official_task_done"
            ):
                return _A_TASK_DONE_TEXT
            if outcome.stop.kind == StopKind.BUDGET_EXHAUSTED and outcome.stop.reason_code == (
                "coin_budget_signal"
            ):
                return _A_BUDGET_EXHAUSTED_TEXT
            if outcome.stop.kind == StopKind.BUDGET_EXHAUSTED and outcome.stop.reason_code == (
                "model_call_limit"
            ):
                return _A_MAX_TURNS_TEXT
            return f"Stopped: {outcome.stop.kind}:{outcome.stop.reason_code}"
        return ""


def _answer_text(result: ToolResult) -> str:
    if result.status != "success":
        return result.content_json
    try:
        return str(json.loads(result.content_json).get("answer", ""))
    except json.JSONDecodeError:
        return result.content_json


class BirdSystemServerAdapter:
    """In-memory harness implementing the frozen system-agent session contract."""

    def __init__(
        self,
        *,
        factory: BirdRuntimeFactory,
        attempt_id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._factory = factory
        self._attempt_ids: Callable[[], UUID] = attempt_id_factory or uuid4
        self._sessions: dict[tuple[str, str], _Session] = {}

    def init_session(self, request: BirdInitSessionRequest) -> BirdInitSessionResponse:
        mode = _INTERNAL_MODE[request.mode]
        key = (mode, request.task_id)
        existing = self._sessions.get(key)
        if existing is not None and not request.reset:
            return BirdInitSessionResponse(
                task_id=request.task_id,
                mode=request.mode,
                session_id=existing.session_id,
                adk_available=True,
            )
        session = _Session(
            factory=self._factory,
            mode=mode,
            task_id=request.task_id,
            state=dict(request.state or {}),
            attempt_ids=self._attempt_ids,
        )
        self._sessions[key] = session
        return BirdInitSessionResponse(
            task_id=request.task_id,
            mode=request.mode,
            session_id=session.session_id,
            adk_available=True,
        )

    async def run_session(self, request: BirdRunSessionRequest) -> BirdRunSessionResponse:
        mode = _INTERNAL_MODE[request.mode]
        session = self._sessions.get((mode, request.task_id))
        if session is None:
            self.init_session(
                BirdInitSessionRequest(
                    task_id=request.task_id, mode=request.mode, state={}, reset=False
                )
            )
            session = self._sessions[(mode, request.task_id)]
        text = await session.run(request.message)
        return BirdRunSessionResponse(
            task_id=request.task_id,
            mode=request.mode,
            session_id=session.session_id,
            response=text,
            state=dict(session.state),
            adk_available=True,
        )
