"""Offline validation and explicit candidate refresh for DeepSeek model config."""

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from commerce_agent.context_builder._canonical import canonical_json
from commerce_agent.context_builder.profiles import ProfileRegistry, RunProfileKey
from commerce_agent.model._pricing import PriceSnapshot
from commerce_agent.model.gateway import CapabilitySnapshot

CAPABILITY_FILE = "deepseek-flash-capability.v1.json"
PRICE_FILE = "deepseek-flash-price.2026-09-12.json"
TOKENIZER_FILE = "deepseek-tokenizer-artifact.v1.json"
HASH_PATTERN = r"^[0-9a-f]{64}$"
SOURCE_URLS = (
    "https://api-docs.deepseek.com/",
    "https://api-docs.deepseek.com/api/create-chat-completion/",
    "https://api-docs.deepseek.com/guides/thinking_mode/",
    "https://api-docs.deepseek.com/guides/tool_calls/",
    "https://api-docs.deepseek.com/quick_start/pricing/",
)


class SnapshotInvalid(ValueError):
    """A checked-in model snapshot failed closed validation."""


class ValidationResult(BaseModel, frozen=True, extra="forbid"):
    valid: Literal[True]
    capability_revision: str
    price_snapshot_id: str
    estimator_revision: str


class SourceEvidence(BaseModel, frozen=True, extra="forbid"):
    claims: tuple[str, ...] = Field(min_length=1)
    url: str = Field(min_length=1)


class TokenizerManifestEntry(BaseModel, frozen=True, extra="forbid"):
    name: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=HASH_PATTERN)
    size: int = Field(ge=0, le=16 * 1024 * 1024)


class TokenizerManifestExcludedEntry(TokenizerManifestEntry):
    """A hash-locked archive entry that is intentionally never installed."""


class CapabilityConfig(CapabilitySnapshot):
    schema_name: Literal["commerce-agent.model-capability.v3"] = Field(alias="schema")
    reviewed_at: datetime
    evidence_document: Literal[
        "docs/project/research/2026-09-12-deepseek-flash-rename-capability.md"
    ]
    evidence_sha256: str = Field(pattern=HASH_PATTERN)
    source_evidence: tuple[SourceEvidence, ...]
    source_evidence_hashes: tuple[str, ...]
    snapshot_sha256: str = Field(pattern=HASH_PATTERN)

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def validate_reviewed_capability(self) -> "CapabilityConfig":
        expected = {
            "revision": "deepseek-flash-capability-v1",
            "requested_model": "deepseek-flash",
            "response_model": "deepseek-flash",
            "backend_model": "DeepSeek-V4.1-Flash",
            "thinking_efforts": ("low", "high", "max"),
            "thinking_effort_field": "reasoning_effort",
            "provider_user_id_field": "user_id",
            "tool_calling": True,
            "thinking_tool_choice": False,
            "replay_rule": "all_assistant_turns_when_tools",
            "expected_usage_fields": (
                "prompt_tokens",
                "prompt_cache_hit_tokens",
                "prompt_cache_miss_tokens",
                "completion_tokens",
                "completion_tokens_details.reasoning_tokens",
                "total_tokens",
            ),
            "context_token_limit": 1_000_000,
            "output_token_limit": 393_216,
            "source_urls": SOURCE_URLS[:-1],
            "source_content_hashes": (
                "6e2eb037db92ebef6a8f6408d87c12318c973388d6e27321606bb0e67dd67a6c",
                "a09b0e9c9b74a8cb1f62e1006b4931320be9c640cc274af54aa72f0e053b64a5",
                "1c12f92419502c6d950b0f68ef2c679a774b4e3cd66d8a2f26642f52c63ff949",
                "4a541725abf784bf1d5717f49ef14e074bd403f18becf4240eabf1a32c7eb7ca",
            ),
        }
        for field, value in expected.items():
            if getattr(self, field) != value:
                raise ValueError(f"{field} differs from the reviewed capability")
        if tuple(item.url for item in self.source_evidence) != self.source_urls:
            raise ValueError("source evidence URLs do not match reviewed sources")
        evidence_hashes = tuple(
            sha256(canonical_json(item).encode("utf-8")).hexdigest()
            for item in self.source_evidence
        )
        if evidence_hashes != self.source_evidence_hashes:
            raise ValueError("source evidence hashes do not match canonical review evidence")
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
            raise ValueError("reviewed_at must be timezone-aware")
        return self


class PriceConfig(PriceSnapshot):
    schema_name: Literal["commerce-agent.model-price.v2"] = Field(alias="schema")
    requested_model: Literal["deepseek-flash"]
    fetched_at: datetime
    reviewed_at: datetime
    source_url: Literal["https://api-docs.deepseek.com/quick_start/pricing/"]
    evidence_document: Literal[
        "docs/project/research/2026-09-12-deepseek-flash-rename-capability.md"
    ]
    source_evidence: SourceEvidence
    source_content_sha256: str = Field(pattern=HASH_PATTERN)
    source_evidence_sha256: str = Field(pattern=HASH_PATTERN)
    evidence_sha256: str = Field(pattern=HASH_PATTERN)
    snapshot_sha256: str = Field(pattern=HASH_PATTERN)

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def validate_reviewed_prices(self) -> "PriceConfig":
        expected = {
            "snapshot_id": "deepseek-flash-usd-2026-09-12",
            "peak_windows_utc": (("01:00", "04:00"), ("06:00", "10:00")),
            "peak_cache_hit_per_million": Decimal("0.006"),
            "peak_cache_miss_per_million": Decimal("0.3"),
            "peak_output_per_million": Decimal("1.2"),
            "off_peak_cache_hit_per_million": Decimal("0.003"),
            "off_peak_cache_miss_per_million": Decimal("0.15"),
            "off_peak_output_per_million": Decimal("0.6"),
            "source_content_sha256": (
                "755aa9b488d1185cba016ca4de3b3b6b8f593f5e13e5f9f961305289a5c8d242"
            ),
        }
        for field, value in expected.items():
            if getattr(self, field) != value:
                raise ValueError(f"{field} differs from the reviewed price snapshot")
        evidence_hash = sha256(canonical_json(self.source_evidence).encode("utf-8")).hexdigest()
        if evidence_hash != self.source_evidence_sha256:
            raise ValueError("source evidence hash does not match canonical review evidence")
        for field in ("fetched_at", "reviewed_at"):
            value = getattr(self, field)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field} must be timezone-aware")
        return self


class TokenizerManifest(BaseModel, frozen=True, extra="forbid"):
    schema_name: Literal["commerce-agent.tokenizer-artifact.v1"] = Field(alias="schema")
    estimator_revision: str = Field(min_length=1, max_length=128)
    renderer_revision: Literal["deepseek-chat-renderer-v1"]
    url: Literal["https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip"]
    review_status: Literal["pending_network_inspection", "reviewed"]
    archive_sha256: str | None = Field(default=None, pattern=HASH_PATTERN)
    entries: tuple[TokenizerManifestEntry, ...]
    directories: tuple[str, ...] = Field(default=(), max_length=64)
    excluded_entries: tuple[TokenizerManifestExcludedEntry, ...] = Field(default=(), max_length=1)
    reviewed_at: datetime | None
    snapshot_sha256: str = Field(pattern=HASH_PATTERN)

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def validate_review_state(self) -> "TokenizerManifest":
        complete = self.archive_sha256 is not None and bool(self.entries) and self.reviewed_at is not None
        if (self.review_status == "reviewed") != complete:
            raise ValueError("tokenizer review state is incomplete")
        return self


def _load_canonical(path: Path) -> dict[str, object]:
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if raw.strip() != canonical_json(payload):
        raise SnapshotInvalid(f"{path.name} must use canonical JSON")
    return payload


def _validate_self_hash(name: str, payload: dict[str, object]) -> None:
    expected = payload.get("snapshot_sha256")
    body = {key: value for key, value in payload.items() if key != "snapshot_sha256"}
    actual = sha256(canonical_json(body).encode("utf-8")).hexdigest()
    if expected != actual:
        raise SnapshotInvalid(f"{name} snapshot sha256 mismatch")


def validate_model_configs(config_root: Path) -> ValidationResult:
    """Validate every reviewed model snapshot without network access."""
    try:
        raw_capability = _load_canonical(config_root / CAPABILITY_FILE)
        raw_price = _load_canonical(config_root / PRICE_FILE)
        raw_tokenizer = _load_canonical(config_root / TOKENIZER_FILE)
        capability = CapabilityConfig.model_validate(raw_capability)
        prices = PriceConfig.model_validate(raw_price)
        tokenizer = TokenizerManifest.model_validate(raw_tokenizer)
        evidence_path = Path(__file__).parents[1] / capability.evidence_document
        evidence_sha256 = sha256(evidence_path.read_bytes()).hexdigest()
        if capability.evidence_sha256 != evidence_sha256 or prices.evidence_sha256 != evidence_sha256:
            raise SnapshotInvalid("review evidence sha256 mismatch")
        for name, payload in (
            (CAPABILITY_FILE, raw_capability),
            (PRICE_FILE, raw_price),
            (TOKENIZER_FILE, raw_tokenizer),
        ):
            _validate_self_hash(name, payload)
        registry = ProfileRegistry.load(config_root)
        for key in RunProfileKey:
            profile = registry.get(key)
            if profile.capability_revision != capability.revision:
                raise SnapshotInvalid("profile capability revision mismatch")
            if profile.estimator_revision != tokenizer.estimator_revision:
                raise SnapshotInvalid("profile estimator revision mismatch")
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as error:
        if isinstance(error, SnapshotInvalid):
            raise
        raise SnapshotInvalid(str(error)) from error
    return ValidationResult(
        valid=True,
        capability_revision=capability.revision,
        price_snapshot_id=prices.snapshot_id,
        estimator_revision=tokenizer.estimator_revision,
    )


def fetch_candidate(
    client: httpx.Client, *, candidate_output: Path, fetched_at: datetime
) -> None:
    """Capture non-secret official-source hashes for a separate human-review candidate."""
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("fetched_at must be timezone-aware")
    sources = []
    for url in SOURCE_URLS:
        response = client.get(url, follow_redirects=True)
        response.raise_for_status()
        if response.url.scheme != "https" or response.url.host != "api-docs.deepseek.com":
            raise SnapshotInvalid("source redirect left the HTTPS host allowlist")
        sources.append(
            {
                "content_bytes": len(response.content),
                "content_sha256": sha256(response.content).hexdigest(),
                "final_url": str(response.url),
                "url": url,
            }
        )
    candidate = {
        "fetched_at": fetched_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "sources": sources,
        "status": "unreviewed_candidate",
    }
    candidate_output.parent.mkdir(parents=True, exist_ok=True)
    with candidate_output.open("x", encoding="utf-8", newline="\n") as output:
        output.write(canonical_json(candidate))


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--config-root", type=Path, required=True)
    fetch = subparsers.add_parser("fetch-candidate")
    fetch.add_argument("--candidate-output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "validate":
        validate_model_configs(args.config_root)
        return 0
    with httpx.Client(timeout=30) as client:
        fetch_candidate(client, candidate_output=args.candidate_output, fetched_at=datetime.now(UTC))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
