import json
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from commerce_agent.context_builder._tokens import (
    ProvisionedDeepSeekTokenEstimator,
    TokenEstimate,
    TokenizerArtifactManifest,
    TokenizerDataFile,
    TokenizerExcludedFile,
)
from commerce_agent.model.contracts import ChatMessage, ModelRequest, NonThinkingConfig, RunScope


def test_token_estimate_is_closed_frozen_and_nonnegative() -> None:
    estimate = TokenEstimate(input_tokens=12, estimator_revision="test-estimator-v1")
    with pytest.raises(ValidationError):
        estimate.input_tokens = 13
    with pytest.raises(ValidationError):
        TokenEstimate(input_tokens=-1, estimator_revision="test-estimator-v1")
    with pytest.raises(ValidationError):
        TokenEstimate.model_validate(estimate.model_dump() | {"unexpected": True})


def installed_synthetic_tokenizer(tmp_path: Path) -> tuple[Path, TokenizerArtifactManifest]:
    root = tmp_path / "tokenizer"
    root.mkdir()
    tokenizer = {
        "model": {
            "type": "WordLevel",
            "unk_token": "[UNK]",
            "vocab": {"[UNK]": 0, "hello": 1},
        }
    }
    content = json.dumps(tokenizer, sort_keys=True, separators=(",", ":")).encode()
    (root / "tokenizer.json").write_bytes(content)
    manifest = TokenizerArtifactManifest(
        estimator_revision="deepseek-tokenizer-v1",
        renderer_revision="deepseek-chat-renderer-v1",
        files=(
            TokenizerDataFile(
                name="tokenizer.json", sha256=sha256(content).hexdigest(), size=len(content)
            ),
        ),
    )
    return root, manifest


def model_request(*, messages: tuple[ChatMessage, ...]) -> ModelRequest:
    return ModelRequest(
        run_scope=RunScope(
            run_id=UUID("00000000-0000-0000-0000-000000000001"),
            track="retail",
            mode="retail",
            subject_id="retail-demo",
            experiment_id="day3",
            config_hash="a" * 64,
        ),
        attempt_id=UUID("00000000-0000-0000-0000-000000000002"),
        sequence=0,
        inference=NonThinkingConfig(type="disabled", temperature=0, max_output_tokens=128),
        messages=messages,
        timeout_seconds=Decimal(30),
        capability_revision="deepseek-v4-flash-capability-v3",
        provider_user_id="b" * 32,
        prompt_policy_hash="c" * 64,
        rendered_prompt_hash="d" * 64,
        tool_hash="e" * 64,
        context_hash="f" * 64,
        config_hash="a" * 64,
    )


def test_estimator_loads_data_only_and_reports_revision(tmp_path: Path) -> None:
    root, manifest = installed_synthetic_tokenizer(tmp_path)
    estimator = ProvisionedDeepSeekTokenEstimator(root, manifest)
    estimate = estimator.estimate(model_request(messages=(ChatMessage(role="user", content="hello"),)))
    assert estimate.input_tokens > 0
    assert estimate.estimator_revision == manifest.estimator_revision
    assert "cost" not in TokenEstimate.model_fields
    (root / "tokenizer.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed manifest"):
        ProvisionedDeepSeekTokenEstimator(root, manifest)


def test_estimator_accepts_nested_reviewed_tokenizer_path(tmp_path: Path) -> None:
    root, manifest = installed_synthetic_tokenizer(tmp_path)
    nested = root / "deepseek_v4_tokenizer"
    nested.mkdir()
    content = (root / "tokenizer.json").read_bytes()
    (nested / "tokenizer.json").write_bytes(content)
    (root / "tokenizer.json").unlink()
    nested_manifest = manifest.model_copy(
        update={
            "files": (
                TokenizerDataFile(
                    name="deepseek_v4_tokenizer/tokenizer.json",
                    sha256=sha256(content).hexdigest(),
                    size=len(content),
                ),
            ),
            "excluded_entries": (
                TokenizerExcludedFile(
                    name="deepseek_v4_tokenizer/deepseek_tokenizer.py",
                    sha256="0" * 64,
                    size=1,
                ),
            ),
        }
    )
    estimator = ProvisionedDeepSeekTokenEstimator(root, nested_manifest)
    assert estimator.revision == "deepseek-tokenizer-v1"
