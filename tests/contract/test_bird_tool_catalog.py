"""The BIRD tool catalogs handed to the model must be official-action clean.

Task 13 pre-run regression (2026-09-13): the bird_a profile still offered the
Day 3 synthetic catalog, so the first real A-mode tool call died with
`action_not_allowlisted` (HttpBirdToolPort correctly refuses non-official
action names). This guard pins every BIRD profile catalog (and every
inference rule's tool list) to the frozen official contract's actions.
"""

from __future__ import annotations

import json
from pathlib import Path

from commerce_agent.context_builder.profiles import ProfileRegistry

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
