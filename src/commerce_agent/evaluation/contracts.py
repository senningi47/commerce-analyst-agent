"""Frozen BIRD-Interact official contract for the custom system agent.

The fixture `tests/fixtures/bird/official-contract.v1.json` is generated
mechanically from the pinned upstream revision by
`scripts/freeze_bird_official_contract.py` and reviewed by hand. The loader
fails closed on revision or schema drift: no default values, no silent
field additions.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

BIRD_SOURCE_REVISION = "451fe2c3518ee1cf908d8139e2913483bd519381"
_FIXTURE_PATH = Path("tests/fixtures/bird/official-contract.v1.json")
_FIXTURE_FILE = Path(__file__).resolve().parents[3] / _FIXTURE_PATH


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
