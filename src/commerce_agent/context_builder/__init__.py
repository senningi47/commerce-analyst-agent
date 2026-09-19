"""Deterministic, profile-isolated model context assembly."""

from commerce_agent.context_builder.builder import ContextBuilder
from commerce_agent.context_builder.contracts import (
    ContextDatum,
    ContextRequest,
    DataNamespace,
    DatumKind,
    PromptBundle,
    PromptStep,
    RunProfile,
    RunProfileKey,
    TrimmingSummary,
    TypedErrorDatum,
)

__all__ = [
    "ContextBuilder",
    "ContextDatum",
    "ContextRequest",
    "DataNamespace",
    "DatumKind",
    "PromptBundle",
    "PromptStep",
    "RunProfile",
    "RunProfileKey",
    "TrimmingSummary",
    "TypedErrorDatum",
]
