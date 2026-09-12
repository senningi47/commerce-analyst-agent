"""Production wiring for the bird system agent container.

Composes the Day 3 modules with the frozen official contract: DeepSeek gateway
(capability/price snapshots from pinned config revisions), in-memory provider
turn store (§15.1: BIRD attempts keep provider state in isolated process
memory only), the HTTP tool port, and the provisioned data-only tokenizer
estimator. Every secret arrives through the environment and never reaches
prompts, state, or logs.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx

from commerce_agent.context_builder._tokens import (
    ProvisionedDeepSeekTokenEstimator,
    TokenizerArtifactManifest,
)
from commerce_agent.context_builder.builder import ContextBuilder, compute_config_hash
from commerce_agent.context_builder.profiles import ProfileRegistry
from commerce_agent.model._pricing import PriceSnapshot
from commerce_agent.model._retry import RetryPolicy
from commerce_agent.model._turn_store import InMemoryProviderTurnStore
from commerce_agent.model.contracts import RunScope
from commerce_agent.model.gateway import CapabilitySnapshot, DeepSeekModelGateway
from commerce_agent.orchestration.bird_a_graph import BirdAGraph, BirdAModelTurnGate
from commerce_agent.orchestration.bird_c_responder import BirdCResponder
from commerce_agent.orchestration.bird_tools_http import (
    BirdHttpResponse,
    BirdToolEndpoint,
    HttpBirdToolPort,
)

CAPABILITY_SNAPSHOT = "deepseek-v4-flash-capability.v3.json"
PRICE_SNAPSHOT = "deepseek-v4-flash-price.2026-09-06.json"
TOKENIZER_MANIFEST = "deepseek-tokenizer-artifact.v1.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


async def _sleep_seconds(seconds: Decimal) -> None:
    await asyncio.sleep(float(seconds))


class HttpxBirdTransport:
    """Production transport for `HttpBirdToolPort` (official services, no proxy)."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def post_json(
        self, url: str, payload: dict[str, Any] | Any, timeout_seconds: float
    ) -> BirdHttpResponse:
        response = await self._client.post(url, json=payload, timeout=timeout_seconds)
        return BirdHttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response.content,
        )


class DeepSeekBirdRuntimeFactory:
    """Concrete `BirdRuntimeFactory` for the real Pilot path."""

    def __init__(
        self,
        *,
        config_root: Path,
        cache_root: Path,
        deepseek_base_url: str,
        deepseek_api_key: str,
        model: str,
        db_env_base_url: str,
        user_sim_base_url: str,
        experiment_id: str,
        provider_user_id: str,
    ) -> None:
        self._registry = ProfileRegistry.load(config_root)
        self._model = model
        self._experiment_id = experiment_id
        self._config_root = config_root
        self._db_env = BirdToolEndpoint(base_url=db_env_base_url)
        self._user_sim = BirdToolEndpoint(base_url=user_sim_base_url)
        capability = CapabilitySnapshot.model_validate(
            _read_json(config_root / CAPABILITY_SNAPSHOT)
        )
        prices = PriceSnapshot.model_validate(_read_json(config_root / PRICE_SNAPSHOT))
        manifest = TokenizerArtifactManifest.model_validate(
            _read_json(config_root / TOKENIZER_MANIFEST)
        )
        estimator = ProvisionedDeepSeekTokenEstimator(
            cache_root / "deepseek-tokenizer", manifest
        )
        self._clock = SystemClock()
        self._turn_store = InMemoryProviderTurnStore(clock=self._clock)
        self._deepseek_client = httpx.AsyncClient(
            base_url=deepseek_base_url,
            headers={"Authorization": f"Bearer {deepseek_api_key}"},
            timeout=120.0,
            trust_env=False,
        )
        self._gateway = DeepSeekModelGateway(
            client=self._deepseek_client,
            turn_store=self._turn_store,
            capability=capability,
            prices=prices,
            retry_policy=RetryPolicy(),
            clock=self._clock,
            sleeper=_sleep_seconds,
            jitter_rng=lambda: Decimal(repr(random.random())),
        )
        self._bird_client = httpx.AsyncClient(trust_env=False)
        self._transport = HttpxBirdTransport(self._bird_client)
        self._context_builder = ContextBuilder(
            registry=self._registry,
            estimator=estimator,
            requested_model=model,
            timeout_seconds=Decimal(120),
            provider_user_id=provider_user_id,
        )

    @classmethod
    def from_env(cls) -> DeepSeekBirdRuntimeFactory:
        api_key = os.environ["DEEPSEEK_API_KEY"]  # fail fast when missing
        return cls(
            config_root=Path(os.environ.get("BIRD_CONFIG_ROOT", "/app/configs/model")),
            cache_root=Path(os.environ.get("BIRD_CACHE_ROOT", "/app/.cache/commerce-agent")),
            deepseek_base_url=os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
            deepseek_api_key=api_key,
            model=os.environ.get("SYSTEM_AGENT_MODEL", "deepseek-v4-flash"),
            db_env_base_url=os.environ.get("DB_ENV_BASE_URL", "http://127.0.0.1:6002"),
            user_sim_base_url=os.environ.get("USER_SIM_BASE_URL", "http://127.0.0.1:6001"),
            experiment_id=os.environ.get("BIRD_EXPERIMENT_ID", "bird-system-agent"),
            provider_user_id=os.environ.get(
                "DEEPSEEK_PROVIDER_USER_ID", "bird-system-agent" + "0" * 15
            ),
        )

    async def aclose(self) -> None:
        await self._deepseek_client.aclose()
        await self._bird_client.aclose()

    def build_run_scope(self, *, mode: Literal["a", "c"], task_id: str) -> RunScope:
        profile = self._registry.get("bird_c" if mode == "c" else "bird_a")
        inference = next(rule.inference for rule in profile.inference_rules)
        return RunScope(
            run_id=uuid4(),
            track="bird",
            mode=mode,
            subject_id=task_id[:128],
            experiment_id=self._experiment_id,
            config_hash=compute_config_hash(profile, inference, self._model),
        )

    def build_tool_port(self, *, task_id: str) -> HttpBirdToolPort:
        return HttpBirdToolPort(
            db_env=self._db_env,
            user_sim=self._user_sim,
            task_id=task_id,
            transport=self._transport,
        )

    def build_c(self, *, run_scope: RunScope) -> BirdCResponder:
        return BirdCResponder(
            context_builder=self._context_builder,
            profile=self._registry.get("bird_c"),
            gateway=self._gateway,
            turn_store=self._turn_store,
        )

    def build_a(
        self,
        *,
        run_scope: RunScope,
        attempt_id: Any,
        tool_port: HttpBirdToolPort,
        max_model_calls: int,
        max_tool_calls: int,
        model_turn_gate: BirdAModelTurnGate | None = None,
    ) -> BirdAGraph:
        # per-attempt call budgets ride on BirdARunRequest (adapter-owned),
        # so the factory ignores them here by contract
        del run_scope, attempt_id, max_model_calls, max_tool_calls
        return BirdAGraph(
            context_builder=self._context_builder,
            profile=self._registry.get("bird_a"),
            gateway=self._gateway,
            tool_port=tool_port,
            turn_store=self._turn_store,
            model_turn_gate=model_turn_gate,
        )
