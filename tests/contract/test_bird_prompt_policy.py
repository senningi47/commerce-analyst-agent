"""The BIRD prompt policies must carry the official task strategy.

Day 6 Task 5 (2026-09-14): the Pilot's rewards were all zero because the
policies omitted the official strategy layer - bird_a never saw the coin
costs or the verify-before-submit tip (blind exploration burned the budget),
bird_c never saw the clarification cap (539 ask_user calls, 8/9 sets without
a submission). These guards pin the integrated strategy to the frozen
official contract and keep the isolation envelopes intact.
"""

from __future__ import annotations

import re
from pathlib import Path

from commerce_agent.context_builder.profiles import ProfileRegistry
from commerce_agent.evaluation.contracts import load_official_contract

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = REPO_ROOT / "configs" / "model"

BIRD_A_ENVELOPE = (
    "Operate only on the synthetic BirdA manifest and supplied agent-visible "
    "records. Use an active tool loop until a shared stop primitive fires. "
    "Product knowledge, Product SQL, ops permissions, and BirdC phase policy "
    "are unavailable."
)
BIRD_C_ENVELOPE = (
    "For the current synthetic phase, return exactly one ask_user or "
    "submit_sql call. Do not run an autonomous loop and do not use Product or "
    "BirdA tools."
)


def test_bird_a_policy_keeps_isolation_envelope() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    common, body = registry.policy_for("bird_a")
    assert common
    assert body.startswith(BIRD_A_ENVELOPE)


def test_bird_c_policy_keeps_isolation_envelope() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    _, body = registry.policy_for("bird_c")
    assert body.startswith(BIRD_C_ENVELOPE)


def test_bird_a_policy_coin_costs_match_frozen_contract() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    _, body = registry.policy_for("bird_a")
    stated = {
        name: float(cost)
        for name, cost in re.findall(r"([a-z_]+)=(\d+(?:\.\d+)?)", body)
    }
    expected = {
        action.name: float(action.coin_cost)
        for action in load_official_contract().actions
    }
    assert stated == expected


def test_bird_a_policy_carries_official_strategy_tips() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    _, body = registry.policy_for("bird_a")
    assert "execute_sql before submit_sql" in body
    assert "budget_remaining" in body
    assert "debug it and submit again" in body


def test_bird_c_policy_declares_clarification_budget() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    _, body = registry.policy_for("bird_c")
    assert "max_turn" in body
    assert "one question per ask_user call" in body
    assert "must call submit_sql" in body


def test_retail_policy_body_is_untouched_by_bird_strategy() -> None:
    registry = ProfileRegistry.load(CONFIG_ROOT)
    _, body = registry.policy_for("retail")
    assert body.startswith("Analyze only Olist Product evidence.")
    assert "max_turn" not in body
    assert "coin" not in body.lower()
