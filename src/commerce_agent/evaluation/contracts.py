"""Frozen BIRD-Interact official contract for the custom system agent.

The fixture `tests/fixtures/bird/official-contract.v1.json` is generated
mechanically from the pinned upstream revision by
`scripts/freeze_bird_official_contract.py` and reviewed by hand. The loader
fails closed on revision or schema drift: no default values, no silent
field additions.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from commerce_agent.model.contracts import Cost, ReportedUsage

BIRD_SOURCE_REVISION = "451fe2c3518ee1cf908d8139e2913483bd519381"
_FIXTURE_PATH = Path("tests/fixtures/bird/official-contract.v1.json")
_FIXTURE_FILE = Path(__file__).resolve().parents[3] / _FIXTURE_PATH


class EvalTaskStatus(StrEnum):
    """Official §16.1 task-attempt states; terminal non-retryable pair: succeeded/failed."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    INTERRUPTED = "interrupted"


TERMINAL_STATUSES = frozenset({EvalTaskStatus.SUCCEEDED, EvalTaskStatus.FAILED})
RETRYABLE_STATUSES = frozenset(
    {
        EvalTaskStatus.PENDING,
        EvalTaskStatus.RUNNING,
        EvalTaskStatus.INFRASTRUCTURE_ERROR,
        EvalTaskStatus.INTERRUPTED,
    }
)

ExperimentPurpose = Literal["pilot", "full", "ablation_repair", "rag_ab", "product"]


class EvalStateConflict(RuntimeError):
    """Raised when an optimistic status guard or identity constraint fails."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class AttemptTelemetry(BaseModel, frozen=True, extra="forbid"):
    """Per-attempt public telemetry (v0.3 §16.3); never carries GT content."""

    model_config = ConfigDict(frozen=True)

    agent_usage: ReportedUsage | None = None
    simulator_usage: ReportedUsage | None = None
    agent_cost: Cost | None = None
    simulator_cost: Cost | None = None
    retry_cost: Cost | None = None
    wall_clock_ms: int | None = Field(default=None, ge=0)
    rounds: int | None = Field(default=None, ge=0)
    tool_calls: int | None = Field(default=None, ge=0)
    submit_count: int | None = Field(default=None, ge=0)
    cache_hit_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    spool_path: str | None = Field(default=None, min_length=1, max_length=512)
    spool_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    # Day 6 spool-import bindings (agent-side session identity + turn count)
    agent_turns: int | None = Field(default=None, ge=0)
    agent_session_id: str | None = Field(default=None, min_length=1, max_length=64)


class AttemptRecord(BaseModel, frozen=True, extra="forbid"):
    model_config = ConfigDict(frozen=True)

    attempt_id: UUID
    run_id: UUID
    experiment_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    mode: Literal["c", "a"]
    attempt_seq: int = Field(ge=1)
    status: EvalTaskStatus = EvalTaskStatus.PENDING
    error_class: str | None = Field(default=None, min_length=1, max_length=128)
    started_at: datetime
    finished_at: datetime | None = None
    telemetry: AttemptTelemetry = AttemptTelemetry()


class EpisodeResult(BaseModel, frozen=True, extra="forbid"):
    """Public per-task result derived only from the official feedback whitelist."""

    model_config = ConfigDict(frozen=True)

    reward: Decimal | None = Field(default=None, ge=0)
    phase1_passed: bool | None = None
    phase2_passed: bool | None = None
    rounds: int | None = Field(default=None, ge=0)
    tool_calls: int | None = Field(default=None, ge=0)
    submit_count: int | None = Field(default=None, ge=0)


class BirdActionContract(BaseModel):
    """One a-interact tool action with its frozen bird-coin cost."""

    model_config = ConfigDict(frozen=True)

    name: str
    coin_cost: Decimal


class BirdEndpointContract(BaseModel):
    """One frozen outbound official endpoint bound to an action."""

    model_config = ConfigDict(frozen=True)

    action: str
    service: Literal["db_env", "user_sim"]
    path: str
    request_fields: frozenset[str]
    response_keys: frozenset[str]
    timeout_seconds: float


class OrchestratorCliContract(BaseModel):
    """The frozen official orchestrator invocation surface."""

    model_config = ConfigDict(frozen=True)

    module: str
    argv_pattern: tuple[str, ...]
    json_output_fields: frozenset[str]


class BirdOfficialContract(BaseModel):
    """The complete frozen official contract consumed by Day 5 modules."""

    model_config = ConfigDict(frozen=True)

    source_revision: str
    init_session_request_fields: frozenset[str]
    init_session_response_fields: frozenset[str]
    run_session_request_fields: frozenset[str]
    run_session_response_fields: frozenset[str]
    submit_sql_response_fields: frozenset[str]
    actions: tuple[BirdActionContract, ...]
    outbound_endpoints: tuple[BirdEndpointContract, ...]
    orchestrator_cli: OrchestratorCliContract
    orchestrator_env_names: frozenset[str]


def load_official_contract() -> BirdOfficialContract:
    """Load and validate the frozen contract fixture; fail closed on drift."""

    payload = json.loads(_FIXTURE_FILE.read_text(encoding="utf-8"))
    if payload.get("source_revision") != BIRD_SOURCE_REVISION:
        raise ValueError("bird official contract revision drift")
    system_agent = payload["system_agent"]
    orchestrator = payload["orchestrator"]
    return BirdOfficialContract(
        source_revision=payload["source_revision"],
        init_session_request_fields=frozenset(
            system_agent["init_session"]["request_fields"]
        ),
        init_session_response_fields=frozenset(
            system_agent["init_session"]["response_fields"]
        ),
        run_session_request_fields=frozenset(
            system_agent["run_session"]["request_fields"]
        ),
        run_session_response_fields=frozenset(
            system_agent["run_session"]["response_fields"]
        ),
        submit_sql_response_fields=frozenset(payload["submit_sql_response_fields"]),
        actions=tuple(
            BirdActionContract(name=entry["name"], coin_cost=Decimal(str(entry["coin_cost"])))
            for entry in payload["actions"]
        ),
        outbound_endpoints=tuple(
            BirdEndpointContract(
                action=entry["action"],
                service=entry["service"],
                path=entry["path"],
                request_fields=frozenset(entry["request_fields"]),
                response_keys=frozenset(entry["response_keys"]),
                timeout_seconds=entry["timeout_seconds"],
            )
            for entry in payload["outbound_endpoints"]
        ),
        orchestrator_cli=OrchestratorCliContract(
            module=orchestrator["module"],
            argv_pattern=tuple(orchestrator["argv_pattern"]),
            json_output_fields=frozenset(orchestrator["json_output_fields"]),
        ),
        orchestrator_env_names=frozenset(orchestrator["env_names"]),
    )
