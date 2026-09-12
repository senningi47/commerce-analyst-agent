import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from shutil import copytree

import httpx
import pytest

from commerce_agent.context_builder._canonical import canonical_json
from scripts.snapshot_deepseek_model_config import (
    SnapshotInvalid,
    fetch_candidate,
    validate_model_configs,
)

CONFIG_ROOT = Path(__file__).parents[3] / "configs" / "model"


def copy_checked_in_model_configs(tmp_path: Path) -> Path:
    root = tmp_path / "model"
    copytree(CONFIG_ROOT, root)
    return root


def mutate_json(path: Path, key: str, value: object) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[key] = value
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def mutate_and_rehash(path: Path, key: str, value: object) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[key] = value
    payload["snapshot_sha256"] = sha256(
        canonical_json({name: item for name, item in payload.items() if name != "snapshot_sha256"}).encode()
    ).hexdigest()
    path.write_text(canonical_json(payload), encoding="utf-8")


def test_validate_mode_recomputes_every_snapshot_hash(tmp_path: Path) -> None:
    root = copy_checked_in_model_configs(tmp_path)
    assert validate_model_configs(root).valid is True
    mutate_json(root / "deepseek-flash-price.2026-09-12.json", "currency", "CNY")
    with pytest.raises(SnapshotInvalid, match="currency"):
        validate_model_configs(root)
    root = copy_checked_in_model_configs(tmp_path / "hash-check")
    mutate_json(
        root / "deepseek-flash-price.2026-09-12.json",
        "reviewed_at",
        "2026-09-01T00:00:01Z",
    )
    with pytest.raises(SnapshotInvalid, match="sha256"):
        validate_model_configs(root)


def test_reviewed_capability_versions_provider_wire_mapping() -> None:
    result = validate_model_configs(CONFIG_ROOT)
    payload = json.loads(
        (CONFIG_ROOT / "deepseek-flash-capability.v1.json").read_text(encoding="utf-8")
    )

    assert result.capability_revision == "deepseek-flash-capability-v1"
    assert payload["schema"] == "commerce-agent.model-capability.v3"
    assert payload["response_model"] == "deepseek-flash"
    assert payload["backend_model"] == "DeepSeek-V4.1-Flash"
    assert payload["thinking_effort_field"] == "reasoning_effort"
    assert payload["provider_user_id_field"] == "user_id"


def test_fetch_candidate_writes_separate_file_without_overwriting_reviewed_snapshots(
    tmp_path: Path,
) -> None:
    root = copy_checked_in_model_configs(tmp_path)
    reviewed_before = {
        path.name: path.read_bytes() for path in root.glob("deepseek-flash-*.json")
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=f"reviewed source {request.url.path}".encode())

    output = tmp_path / "candidate.json"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fetch_candidate(
            client,
            candidate_output=output,
            fetched_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
    candidate = json.loads(output.read_text(encoding="utf-8"))
    assert candidate["status"] == "unreviewed_candidate"
    assert candidate["sources"]
    assert reviewed_before == {
        path.name: path.read_bytes() for path in root.glob("deepseek-flash-*.json")
    }


@pytest.mark.parametrize(
    ("filename", "key", "value"),
    [
        ("deepseek-flash-capability.v1.json", "response_model", "other-model"),
        (
            "deepseek-flash-capability.v1.json",
            "source_urls",
            [
                "https://example.com/",
                "https://api-docs.deepseek.com/api/create-chat-completion/",
                "https://api-docs.deepseek.com/guides/thinking_mode/",
                "https://api-docs.deepseek.com/guides/tool_calls/",
            ],
        ),
        (
            "deepseek-flash-capability.v1.json",
            "source_content_hashes",
            ["0" * 64] * 4,
        ),
        ("deepseek-flash-price.2026-09-12.json", "peak_output_per_million", "9.99"),
    ],
)
def test_validate_rejects_unapproved_semantics_even_with_recomputed_hash(
    tmp_path: Path, filename: str, key: str, value: object
) -> None:
    root = copy_checked_in_model_configs(tmp_path)
    mutate_and_rehash(root / filename, key, value)
    with pytest.raises(SnapshotInvalid):
        validate_model_configs(root)
