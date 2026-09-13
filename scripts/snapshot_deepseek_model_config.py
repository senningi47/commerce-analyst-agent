"""Offline validation and explicit candidate refresh for DeepSeek model config."""

import argparse
import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ValidationError

from commerce_agent.context_builder._canonical import canonical_json
from commerce_agent.context_builder.profiles import ProfileRegistry, RunProfileKey
from commerce_agent.model.snapshots import (
    CAPABILITY_FILE,
    PRICE_FILE,
    SOURCE_URLS,
    TOKENIZER_FILE,
    CapabilityConfig,
    PriceConfig,
    SnapshotInvalid,
    TokenizerManifest,
    load_canonical_snapshot,
    validate_snapshot_self_hash,
)

__all__ = [
    "CAPABILITY_FILE",
    "PRICE_FILE",
    "SOURCE_URLS",
    "TOKENIZER_FILE",
    "CapabilityConfig",
    "PriceConfig",
    "SnapshotInvalid",
    "TokenizerManifest",
    "fetch_candidate",
    "validate_model_configs",
]


class ValidationResult(BaseModel, frozen=True, extra="forbid"):
    valid: Literal[True]
    capability_revision: str
    price_snapshot_id: str
    estimator_revision: str


def validate_model_configs(config_root: Path) -> ValidationResult:
    """Validate every reviewed model snapshot without network access."""
    try:
        raw_capability = load_canonical_snapshot(config_root / CAPABILITY_FILE)
        raw_price = load_canonical_snapshot(config_root / PRICE_FILE)
        raw_tokenizer = load_canonical_snapshot(config_root / TOKENIZER_FILE)
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
            validate_snapshot_self_hash(name, payload)
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
