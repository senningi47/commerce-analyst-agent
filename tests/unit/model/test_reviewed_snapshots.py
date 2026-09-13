"""Checked-in reviewed snapshots must load through the runtime seam.

Task 13 pre-run regression (2026-09-13): the bird system agent runtime loaded
the rich review-format snapshot files with the narrow gateway models and
failed closed on the first real container start. The reviewed-file models now
live in `commerce_agent.model.snapshots`; these tests pin the actual
checked-in files to that seam so a snapshot can never ship again without
passing the loader the runtime uses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from commerce_agent.context_builder._canonical import canonical_json
from commerce_agent.context_builder._tokens import TokenizerArtifactManifest
from commerce_agent.model._pricing import PriceSnapshot
from commerce_agent.model.gateway import CapabilitySnapshot
from commerce_agent.model.snapshots import (
    CAPABILITY_FILE,
    SnapshotInvalid,
    load_reviewed_model_snapshots,
    to_tokenizer_artifact_manifest,
)

CONFIG_ROOT = Path(__file__).resolve().parents[3] / "configs" / "model"


def test_load_reviewed_model_snapshots_accepts_checked_in_files() -> None:
    snapshots = load_reviewed_model_snapshots(CONFIG_ROOT)

    assert isinstance(snapshots.capability, CapabilitySnapshot)
    assert isinstance(snapshots.prices, PriceSnapshot)
    assert snapshots.capability.revision == "deepseek-flash-capability-v1"
    assert snapshots.prices.snapshot_id == "deepseek-flash-usd-2026-09-12"
    assert snapshots.tokenizer.review_status == "reviewed"


def test_loader_fails_closed_when_a_snapshot_file_is_missing(tmp_path: Path) -> None:
    with pytest.raises(SnapshotInvalid):
        load_reviewed_model_snapshots(tmp_path)


def test_loader_fails_closed_when_snapshot_content_is_tampered(tmp_path: Path) -> None:
    for name in (CAPABILITY_FILE, "deepseek-flash-price.2026-09-12.json", "deepseek-tokenizer-artifact.v1.json"):
        (tmp_path / name).write_text(
            (CONFIG_ROOT / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    target = tmp_path / CAPABILITY_FILE
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["context_token_limit"] = payload["context_token_limit"] + 1
    del payload["snapshot_sha256"]
    payload["snapshot_sha256"] = "0" * 64
    target.write_text(canonical_json(payload), encoding="utf-8")

    with pytest.raises(SnapshotInvalid):
        load_reviewed_model_snapshots(tmp_path)


def test_reviewed_tokenizer_manifest_converts_to_estimator_manifest() -> None:
    snapshots = load_reviewed_model_snapshots(CONFIG_ROOT)

    manifest = to_tokenizer_artifact_manifest(snapshots.tokenizer)

    assert isinstance(manifest, TokenizerArtifactManifest)
    assert manifest.estimator_revision == snapshots.tokenizer.estimator_revision
    assert len(manifest.files) == len(snapshots.tokenizer.entries)
