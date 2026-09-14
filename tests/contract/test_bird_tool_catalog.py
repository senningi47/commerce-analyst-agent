"""The tool catalogs handed to the model must match graphs, ports, and contracts.

Task 13 pre-run regression (2026-09-13): the bird_a profile still offered the
Day 3 synthetic catalog, so the first real A-mode tool call died with
`action_not_allowlisted` (HttpBirdToolPort correctly refuses non-official
action names). This guard pins every BIRD profile catalog (and every
inference rule's tool list) to the frozen official contract's actions.

Day 6 Task 4 (2026-09-14): extended to three-way consistency — per profile
the run-profile rule tool set, the serving graph/port allowlist, and the
frozen contract fixture must agree exactly, so the model is never offered a
tool its graph would reject and no graph accepts a tool outside the frozen
catalog.
"""

from __future__ import annotations

import json
from pathlib import Path

from commerce_agent.context_builder.profiles import ProfileRegistry
from commerce_agent.orchestration.bird_c_responder import BIRD_C_TOOL_NAMES
from commerce_agent.orchestration.retail_graph import RETAIL_TOOL_NAMES

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = REPO_ROOT / "configs" / "model"
CONTRACT_FILE = REPO_ROOT / "tests" / "fixtures" / "bird" / "official-contract.v1.json"


def _official_actions() -> set[str]:
    contract = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))
    return {entry["action"] for entry in contract["outbound_endpoints"]}


def test_bird_profile_catalogs_only_offer_official_actions() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    actions = _official_actions()

    for key in ("bird_a", "bird_c"):
        names = registry.tool_names(key)
        assert names, f"{key} catalog must not be empty"
        assert names <= actions, (
            f"{key} offers non-official tools {sorted(names - actions)}; "
            "the port would fail closed on the first model tool call"
        )


def test_bird_inference_rules_only_reference_official_actions() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    actions = _official_actions()

    for key in ("bird_a", "bird_c"):
        for step, names in registry.step_tools(key).items():
            assert names <= actions, (
                f"{key} step {step} references non-official tools {sorted(names - actions)}"
            )


def test_bird_a_rule_tools_exactly_equal_port_allowlist() -> None:
    """HttpBirdToolPort derives its allowlist from this same frozen fixture."""

    registry = ProfileRegistry.load(CONFIG_ROOT)
    assert registry.step_tools("bird_a")["bird_a_act"] == _official_actions()


def test_bird_c_rule_tools_exactly_equal_responder_allowlist() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    assert registry.step_tools("bird_c")["bird_c_respond"] == set(BIRD_C_TOOL_NAMES)


def test_retail_decide_rule_tools_exactly_equal_graph_allowlist() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    assert registry.step_tools("retail")["retail_decide"] == set(RETAIL_TOOL_NAMES)


def test_retail_graph_allowlist_stays_inside_frozen_catalog() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    assert set(RETAIL_TOOL_NAMES) <= registry.tool_names("retail")
