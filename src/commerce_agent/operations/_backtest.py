"""Deterministic fixed-metric alert backtesting."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from commerce_agent.operations._canonical import canonical_json_bytes, normalized_decimal, utc_z
from commerce_agent.operations.contracts import (
    AlertBacktestRef,
    AlertBacktestRequest,
    AlertBacktestSnapshot,
    AlertComparator,
    AlertMetric,
    AlertWindowResult,
)
from commerce_agent.operations.errors import OperationContractError


@dataclass(frozen=True)
class MetricDefinition:
    revision: str
    numerator: str
    denominator: str
    time_field: str
    allowed_grains: tuple[str, ...]


METRIC_DEFINITIONS = {
    AlertMetric.LATE_DELIVERY_RATE: MetricDefinition(
        revision="metric.late_delivery_rate.v1",
        numerator="delivered_after_estimate",
        denominator="delivered_orders",
        time_field="order_purchase_timestamp",
        allowed_grains=("global", "seller_state", "product_category"),
    ),
    AlertMetric.LOW_RATING_RATE: MetricDefinition(
        revision="metric.low_rating_rate.v1",
        numerator="review_score_lte_2",
        denominator="reviewed_orders",
        time_field="review_creation_date",
        allowed_grains=("global", "seller_state", "product_category"),
    ),
    AlertMetric.CANCELLATION_RATE: MetricDefinition(
        revision="metric.cancellation_rate.v1",
        numerator="cancelled_orders",
        denominator="placed_orders",
        time_field="order_purchase_timestamp",
        allowed_grains=("global", "customer_state"),
    ),
}


@dataclass(frozen=True)
class AlertBacktestQuery:
    definition: MetricDefinition
    request: AlertBacktestRequest


@dataclass(frozen=True)
class AlertObservation:
    window_started_at: datetime
    window_ended_at: datetime
    numerator: int
    denominator: int
    complete: bool


class MetricDefinitionPort(Protocol):
    async def resolve(self, metric: AlertMetric) -> MetricDefinition: ...


class AlertObservationPort(Protocol):
    async def observe(self, query: AlertBacktestQuery) -> tuple[AlertObservation, ...]: ...


class StaticMetricDefinitionPort:
    async def resolve(self, metric: AlertMetric) -> MetricDefinition:
        return METRIC_DEFINITIONS[metric]


class InMemoryAlertObservationPort:
    def __init__(self, observations: tuple[AlertObservation, ...]) -> None:
        self._observations = observations

    async def observe(self, query: AlertBacktestQuery) -> tuple[AlertObservation, ...]:
        del query
        return self._observations


def rule_spec_sha256(request: AlertBacktestRequest) -> str:
    projection = {
        "comparator": request.comparator.value,
        "ended_at": utc_z(request.ended_at),
        "filter_refs": sorted(request.filter_refs),
        "grain": request.grain,
        "metric": request.metric.value,
        "metric_revision": request.metric_revision,
        "minimum_denominator": request.minimum_denominator,
        "schema_version": 1,
        "started_at": utc_z(request.started_at),
        "threshold": normalized_decimal(request.threshold, scale=4),
        "window": request.window.value,
    }
    return hashlib.sha256(canonical_json_bytes(projection)).hexdigest()


class MetricAlertBacktester:
    def __init__(
        self, *, metrics: MetricDefinitionPort, observations: AlertObservationPort
    ) -> None:
        self._metrics = metrics
        self._observations = observations

    async def run(self, request: AlertBacktestRequest) -> AlertBacktestSnapshot:
        definition = await self._metrics.resolve(request.metric)
        if definition.revision != request.metric_revision:
            raise OperationContractError("metric_revision_mismatch", retryable=False)
        if request.grain not in definition.allowed_grains:
            raise OperationContractError("metric_grain_forbidden", retryable=False)
        observations = await self._observations.observe(
            AlertBacktestQuery(definition=definition, request=request)
        )
        self._validate_observation_order(observations, request)
        windows = tuple(self._evaluate(item, request) for item in observations)
        digest = rule_spec_sha256(request)
        return AlertBacktestSnapshot(
            backtest_ref=AlertBacktestRef(
                backtest_id=uuid5(NAMESPACE_URL, f"alert-backtest:{digest}"),
                rule_spec_sha256=digest,
            ),
            request=request,
            windows=windows,
            completed_at=request.ended_at,
        )

    @staticmethod
    def _validate_observation_order(
        observations: tuple[AlertObservation, ...], request: AlertBacktestRequest
    ) -> None:
        previous_end = None
        for item in observations:
            if (
                item.window_started_at < request.started_at
                or item.window_ended_at > request.ended_at
                or item.window_started_at >= item.window_ended_at
                or (previous_end is not None and item.window_started_at < previous_end)
                or item.numerator < 0
                or item.denominator < 0
                or item.numerator > item.denominator
            ):
                raise OperationContractError("backtest_observation_invalid", retryable=False)
            previous_end = item.window_ended_at

    @staticmethod
    def _evaluate(
        observation: AlertObservation, request: AlertBacktestRequest
    ) -> AlertWindowResult:
        excluded_reason = None
        if observation.denominator == 0:
            excluded_reason = "zero_denominator"
        elif observation.denominator < request.minimum_denominator:
            excluded_reason = "below_minimum_denominator"
        elif not observation.complete:
            excluded_reason = "incomplete_window"
        value = (
            None
            if excluded_reason
            else (Decimal(observation.numerator) / Decimal(observation.denominator)).quantize(
                Decimal("0.000001")
            )
        )
        hit = False
        if value is not None:
            if request.comparator is AlertComparator.GREATER_THAN:
                hit = value > request.threshold
            else:
                hit = value >= request.threshold
        return AlertWindowResult(
            window_started_at=observation.window_started_at,
            window_ended_at=observation.window_ended_at,
            numerator=observation.numerator,
            denominator=observation.denominator,
            normalized_value=value,
            complete=observation.complete,
            excluded_reason=excluded_reason,
            hit=hit,
        )


class InMemoryBacktestRegistry:
    def __init__(self, snapshots: tuple[AlertBacktestSnapshot, ...] = ()) -> None:
        self._snapshots = {item.backtest_ref.backtest_id: item for item in snapshots}

    async def resolve(self, reference: AlertBacktestRef) -> AlertBacktestSnapshot | None:
        return self._snapshots.get(reference.backtest_id)

    def add(self, snapshot: AlertBacktestSnapshot) -> None:
        self._snapshots[snapshot.backtest_ref.backtest_id] = snapshot
