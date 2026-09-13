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
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx

from commerce_agent.context_builder._tokens import (
    ProvisionedDeepSeekTokenEstimator,
)
from commerce_agent.context_builder.builder import (
    ContextBuilder,
    compute_config_hash,
    scope_digest,
)
from commerce_agent.context_builder.profiles import ProfileRegistry
from commerce_agent.evaluation.spool import SpoolWriter
from commerce_agent.model._retry import RetryPolicy
from commerce_agent.model._turn_store import InMemoryProviderTurnStore
from commerce_agent.model.contracts import (
    ModelGateway,
    ModelRequest,
    ModelResponse,
    RunScope,
)
from commerce_agent.model.gateway import DeepSeekModelGateway
from commerce_agent.model.snapshots import (
    SnapshotInvalid,
    load_reviewed_model_snapshots,
    to_tokenizer_artifact_manifest,
)
from commerce_agent.orchestration.bird_a_graph import BirdAGraph, BirdAModelTurnGate
from commerce_agent.orchestration.bird_c_responder import BirdCResponder
from commerce_agent.orchestration.bird_tools_http import (
    BirdHttpResponse,
    BirdToolEndpoint,
    HttpBirdToolPort,
)


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


class SpoolTraceGateway:
    """ModelGateway decorator feeding the agent-visible JSONL spool (v0.3 §18).

    One spool file per attempt under the mounted spool dir; one public event
    per model turn carrying usage/cost and model identity. These fields never
    enter responses — this channel is their only path off the agent process.
    """

    def __init__(self, inner: ModelGateway, spool_dir: Path) -> None:
        self._inner = inner
        self._spool_dir = spool_dir
        self._writers: dict[UUID, SpoolWriter] = {}

    async def complete(self, request: ModelRequest) -> ModelResponse:
        response = await self._inner.complete(request)
        self._record(request, response)
        return response

    def _record(self, request: ModelRequest, response: ModelResponse) -> None:
        writer = self._writers.get(request.attempt_id)
        if writer is None:
            writer = SpoolWriter(self._spool_dir / f"{request.attempt_id}.jsonl")
            self._writers[request.attempt_id] = writer
        writer.append(
            {
                "run_scope_digest": scope_digest(request.run_scope),
                "attempt_id": str(request.attempt_id),
                "phase": "attempt",
                "sequence": request.sequence,
                "event_type": "model_turn",
                "payload": {
                    "actual_model": response.actual_model,
                    "finish_reason": str(response.finish_reason),
                    "usage": response.usage.model_dump(mode="json"),
                    "cost": response.cost.model_dump(mode="json"),
                },
            }
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
        spool_dir: Path,
    ) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", provider_user_id):
            # ModelRequest.provider_user_id enforces this pattern; failing here
            # beats a ValidationError surfacing inside the first model turn.
            raise ValueError("provider_user_id must be exactly 32 hexadecimal characters")
        self._registry = ProfileRegistry.load(config_root)
        self._model = model
        self._experiment_id = experiment_id
        self._config_root = config_root
        self._db_env = BirdToolEndpoint(base_url=db_env_base_url)
        self._user_sim = BirdToolEndpoint(base_url=user_sim_base_url)
        snapshots = load_reviewed_model_snapshots(config_root)
        capability = snapshots.capability
        prices = snapshots.prices
        tokenizer = snapshots.tokenizer
        if tokenizer.archive_sha256 is None:
            raise SnapshotInvalid("reviewed tokenizer archive is not installed")
        artifact_root = cache_root / "deepseek-tokenizer" / tokenizer.archive_sha256
        marker = json.loads((artifact_root / ".complete.json").read_text(encoding="utf-8"))
        if marker != {
            "archive_sha256": tokenizer.archive_sha256,
            "entries": len(tokenizer.entries),
        }:
            raise SnapshotInvalid("tokenizer install mismatch")
        estimator = ProvisionedDeepSeekTokenEstimator(
            artifact_root, to_tokenizer_artifact_manifest(tokenizer)
        )
        self._clock = SystemClock()
        self._turn_store = InMemoryProviderTurnStore(clock=self._clock)
        self._deepseek_client = httpx.AsyncClient(
            base_url=deepseek_base_url,
            headers={"Authorization": f"Bearer {deepseek_api_key}"},
            timeout=120.0,
            trust_env=False,
        )
        self._gateway = SpoolTraceGateway(
            DeepSeekModelGateway(
                client=self._deepseek_client,
                turn_store=self._turn_store,
                capability=capability,
                prices=prices,
                retry_policy=RetryPolicy(),
                clock=self._clock,
                sleeper=_sleep_seconds,
                jitter_rng=lambda: Decimal(repr(random.random())),
            ),
            spool_dir=spool_dir,
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
            model=os.environ.get("SYSTEM_AGENT_MODEL", "deepseek-flash"),
            db_env_base_url=os.environ.get("DB_ENV_BASE_URL", "http://127.0.0.1:6002"),
            user_sim_base_url=os.environ.get("USER_SIM_BASE_URL", "http://127.0.0.1:6001"),
            experiment_id=os.environ.get("BIRD_EXPERIMENT_ID", "bird-system-agent"),
            provider_user_id=os.environ.get("DEEPSEEK_PROVIDER_USER_ID", "0" * 32),
            spool_dir=Path(os.environ["BIRD_SPOOL_DIR"]),  # fail fast when missing
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
