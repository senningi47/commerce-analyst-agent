"""Bird runtime factory construction guards.

Task 13 pre-run regression (2026-09-13): the compose default for
DEEPSEEK_PROVIDER_USER_ID was not 32-hex, and the invalid value surfaced only
as a ValidationError deep inside the first run_session turn (mapped to a
generic 400). The factory must fail fast at construction.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bird_system_agent.runtime import DeepSeekBirdRuntimeFactory

CONFIG_ROOT = Path(__file__).resolve().parents[3] / "configs" / "model"
CACHE_ROOT = Path(__file__).resolve().parents[3] / ".cache" / "commerce-agent"


def _build(provider_user_id: str, tmp_path: Path) -> DeepSeekBirdRuntimeFactory:
    return DeepSeekBirdRuntimeFactory(
        config_root=CONFIG_ROOT,
        cache_root=CACHE_ROOT,
        deepseek_base_url="https://api.deepseek.com",
        deepseek_api_key="test-key",
        model="deepseek-flash",
        db_env_base_url="http://127.0.0.1:6002",
        user_sim_base_url="http://127.0.0.1:6001",
        experiment_id="factory-guard",
        provider_user_id=provider_user_id,
        spool_dir=tmp_path,
    )


def test_factory_rejects_non_hex_provider_user_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="provider_user_id"):
        _build("bird-system-agent000000000000000", tmp_path)


def test_factory_accepts_32hex_provider_user_id(tmp_path: Path) -> None:
    factory = _build("0" * 32, tmp_path)
    try:
        assert factory is not None
    finally:
        asyncio.run(factory.aclose())
