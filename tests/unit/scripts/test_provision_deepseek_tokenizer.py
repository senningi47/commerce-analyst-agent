import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from stat import S_IFCHR, S_IFLNK
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import httpx
import pytest

from scripts.provision_deepseek_tokenizer import (
    ArtifactInspection,
    ArtifactRejected,
    build_parser,
    download_and_inspect,
    inspect_archive,
    install_archive,
    load_reviewed_manifest,
    main,
    validate_download_metadata,
    validate_source_url,
)


def make_zip(tmp_path: Path, entries: dict[str, bytes]) -> Path:
    archive = tmp_path / "tokenizer.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)
    return archive


def valid_data_entries() -> dict[str, bytes]:
    return {
        "tokenizer.json": b'{"model":{"type":"WordLevel","vocab":{"hello":0}}}',
        "tokenizer_config.json": b'{"tokenizer_class":"PreTrainedTokenizerFast"}',
    }


def reviewed_manifest_payload(reviewed: ArtifactInspection) -> dict[str, object]:
    payload = {
        "archive_sha256": reviewed.sha256,
        "directories": list(reviewed.directories),
        "entries": [entry.model_dump(mode="json") for entry in reviewed.entries],
        "excluded_entries": [
            entry.model_dump(mode="json") for entry in reviewed.excluded_entries
        ],
        "estimator_revision": "deepseek-tokenizer-v1",
        "renderer_revision": "deepseek-chat-renderer-v1",
        "review_status": "reviewed",
        "reviewed_at": "2026-09-01T00:00:00Z",
        "schema": "commerce-agent.tokenizer-artifact.v1",
        "url": "https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip",
    }
    payload["snapshot_sha256"] = sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return payload


@pytest.mark.parametrize(
    "entry",
    ["../escape.py", "/absolute/tokenizer.json", "nested/../../escape", "run_me.py"],
)
def test_inspector_rejects_unsafe_or_executable_entries(tmp_path: Path, entry: str) -> None:
    archive = make_zip(tmp_path, {entry: b"content"})
    with pytest.raises(ArtifactRejected):
        inspect_archive(archive, maximum_bytes=8 * 1024 * 1024)


@pytest.mark.parametrize("entry", ["native.exe", "loader.dll", "payload.pkl", "setup.sh"])
def test_inspector_rejects_non_data_file_extensions(tmp_path: Path, entry: str) -> None:
    archive = make_zip(tmp_path, {entry: b"content"})
    with pytest.raises(ArtifactRejected, match="data"):
        inspect_archive(archive, maximum_bytes=8 * 1024 * 1024)


def test_known_vendor_demo_is_hash_locked_but_never_installed(tmp_path: Path) -> None:
    archive = make_zip(
        tmp_path,
        valid_data_entries()
        | {"deepseek_v4_tokenizer/deepseek_tokenizer.py": b"print('never execute')"},
    )
    candidate = inspect_archive(archive, maximum_bytes=8 * 1024 * 1024)
    assert [entry.name for entry in candidate.excluded_entries] == [
        "deepseek_v4_tokenizer/deepseek_tokenizer.py"
    ]
    target = install_archive(archive, candidate, tmp_path / "installed")
    assert not (target / "deepseek_v4_tokenizer/deepseek_tokenizer.py").exists()


def test_install_requires_exact_excluded_vendor_entry_set(tmp_path: Path) -> None:
    archive = make_zip(
        tmp_path,
        valid_data_entries()
        | {"deepseek_v4_tokenizer/deepseek_tokenizer.py": b"print('never execute')"},
    )
    candidate = inspect_archive(archive, maximum_bytes=8 * 1024 * 1024)
    tampered = candidate.model_copy(update={"excluded_entries": ()})
    with pytest.raises(ArtifactRejected, match="entry set"):
        install_archive(archive, tampered, tmp_path / "installed")


def test_install_requires_exact_directory_set(tmp_path: Path) -> None:
    archive = tmp_path / "directories.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
        directory = ZipInfo("deepseek_v4_tokenizer/")
        directory.external_attr = 0o755 << 16
        bundle.writestr(directory, b"")
        for name, content in valid_data_entries().items():
            bundle.writestr(name, content)
    candidate = inspect_archive(archive, maximum_bytes=8 * 1024 * 1024)
    assert candidate.directories == ("deepseek_v4_tokenizer/",)
    tampered = candidate.model_copy(update={"directories": ()})
    with pytest.raises(ArtifactRejected, match="entry set"):
        install_archive(archive, tampered, tmp_path / "installed")


def test_install_requires_reviewed_hash_and_exact_entry_set(tmp_path: Path) -> None:
    archive = make_zip(tmp_path, valid_data_entries())
    candidate = inspect_archive(archive, maximum_bytes=8 * 1024 * 1024)
    with pytest.raises(ArtifactRejected, match="sha256"):
        install_archive(
            archive,
            candidate.model_copy(update={"sha256": "0" * 64}),
            tmp_path / "installed",
        )
    incomplete = candidate.model_copy(update={"entries": candidate.entries[:-1]})
    with pytest.raises(ArtifactRejected, match="entry set"):
        install_archive(archive, incomplete, tmp_path / "installed")


def test_inspector_rejects_symlink_entries(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.zip"
    link = ZipInfo("tokenizer.json")
    link.create_system = 3
    link.external_attr = (S_IFLNK | 0o777) << 16
    with ZipFile(archive, "w") as bundle:
        bundle.writestr(link, "target")
    with pytest.raises(ArtifactRejected, match="regular"):
        inspect_archive(archive, maximum_bytes=8 * 1024 * 1024)


@pytest.mark.parametrize(
    "names",
    [
        ("tokenizer.json", "tokenizer.json"),
        ("tokenizer.json", "TOKENIZER.JSON"),
    ],
)
def test_inspector_rejects_duplicate_or_case_colliding_names(
    tmp_path: Path, names: tuple[str, str]
) -> None:
    archive = tmp_path / "duplicates.zip"
    with ZipFile(archive, "w") as bundle:
        for name in names:
            bundle.writestr(name, "{}")
    with pytest.raises(ArtifactRejected, match="duplicate"):
        inspect_archive(archive, maximum_bytes=8 * 1024 * 1024)


def test_inspector_rejects_compression_bombs(tmp_path: Path) -> None:
    archive = make_zip(tmp_path, {"tokenizer.json": b"0" * (1024 * 1024)})
    with pytest.raises(ArtifactRejected, match="compression"):
        inspect_archive(archive, maximum_bytes=8 * 1024 * 1024)


def test_inspector_rejects_total_extracted_bytes_over_limit(tmp_path: Path) -> None:
    archive = tmp_path / "oversized.zip"
    with ZipFile(archive, "w", compression=ZIP_STORED) as bundle:
        bundle.writestr("tokenizer.json", b"0" * (16 * 1024 * 1024 + 1))
    with pytest.raises(ArtifactRejected, match="extracted"):
        inspect_archive(archive, maximum_bytes=32 * 1024 * 1024)


def test_inspector_rejects_more_than_64_entries(tmp_path: Path) -> None:
    archive = make_zip(tmp_path, {f"data/{index}.json": b"{}" for index in range(65)})
    with pytest.raises(ArtifactRejected, match="64"):
        inspect_archive(archive, maximum_bytes=8 * 1024 * 1024)


def test_inspector_rejects_archive_without_regular_data_files(tmp_path: Path) -> None:
    archive = tmp_path / "empty.zip"
    with ZipFile(archive, "w"):
        pass
    with pytest.raises(ArtifactRejected, match="regular data"):
        inspect_archive(archive, maximum_bytes=8 * 1024 * 1024)


def test_inspector_rejects_non_regular_device_entries(tmp_path: Path) -> None:
    archive = tmp_path / "device.zip"
    device = ZipInfo("tokenizer.json")
    device.create_system = 3
    device.external_attr = (S_IFCHR | 0o600) << 16
    with ZipFile(archive, "w") as bundle:
        bundle.writestr(device, "")
    with pytest.raises(ArtifactRejected, match="regular"):
        inspect_archive(archive, maximum_bytes=8 * 1024 * 1024)


@pytest.mark.parametrize(
    "url",
    [
        "http://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip",
        "https://example.com/api-docs/deepseek_v4_tokenizer.zip",
        "https://cdn.deepseek.com/other.zip",
    ],
)
def test_source_url_is_exact_https_allowlisted_url(url: str) -> None:
    with pytest.raises(ArtifactRejected, match="URL"):
        validate_source_url(url)


def test_download_rejects_redirect_away_from_allowlisted_host() -> None:
    with pytest.raises(ArtifactRejected, match="redirect"):
        validate_download_metadata(
            final_url="https://example.com/tokenizer.zip",
            redirect_chain=(
                "https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip",
            ),
            content_length=1024,
            maximum_bytes=8 * 1024 * 1024,
        )


def test_download_rejects_declared_content_length_over_limit() -> None:
    with pytest.raises(ArtifactRejected, match="content length"):
        validate_download_metadata(
            final_url="https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip",
            redirect_chain=(),
            content_length=8 * 1024 * 1024 + 1,
            maximum_bytes=8 * 1024 * 1024,
        )


def test_install_extracts_reviewed_data_and_writes_completion_marker(tmp_path: Path) -> None:
    archive = make_zip(tmp_path, valid_data_entries())
    candidate = inspect_archive(archive, maximum_bytes=8 * 1024 * 1024)
    target = install_archive(archive, candidate, tmp_path / "installed")
    assert (target / "tokenizer.json").read_bytes() == valid_data_entries()["tokenizer.json"]
    assert (target / ".complete.json").is_file()
    with pytest.raises(FileExistsError):
        install_archive(archive, candidate, target)


def test_inspect_download_writes_provenance_candidate_without_installing(tmp_path: Path) -> None:
    archive = make_zip(tmp_path, valid_data_entries())
    body = archive.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip"
        return httpx.Response(
            200,
            headers={
                "content-length": str(len(body)),
                "last-modified": "Tue, 01 Sep 2026 00:00:00 GMT",
            },
            content=body,
        )

    candidate_output = tmp_path / "candidate.json"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        candidate = download_and_inspect(
            client,
            candidate_output=candidate_output,
            inspected_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
    persisted = json.loads(candidate_output.read_text(encoding="utf-8"))
    assert persisted["archive_sha256"] == candidate.sha256
    assert persisted["directories"] == list(candidate.directories)
    assert persisted["regular_files"] == [entry.name for entry in candidate.entries]
    assert persisted["excluded_entries"] == [
        entry.model_dump(mode="json") for entry in candidate.excluded_entries
    ]
    assert persisted["redirect_chain"] == []
    assert not (tmp_path / ".cache").exists()


def test_install_rejects_manifest_pending_human_review(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "archive_sha256": None,
                "entries": [],
                "review_status": "pending_network_inspection",
                "url": "https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ArtifactRejected, match="reviewed"):
        load_reviewed_manifest(manifest)


@pytest.mark.parametrize("command", ["inspect", "install"])
def test_cli_exposes_explicit_inspect_and_install_modes(command: str) -> None:
    parser = build_parser()
    option = "--candidate-output" if command == "inspect" else "--manifest"
    parsed = parser.parse_args([command, option, "artifact.json"])
    assert parsed.command == command


def test_install_cli_checks_review_before_creating_http_client(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "archive_sha256": None,
                "entries": [],
                "review_status": "pending_network_inspection",
                "url": "https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip",
            }
        ),
        encoding="utf-8",
    )

    def forbidden_client() -> httpx.Client:
        raise AssertionError("HTTP client must not be created before manifest review")

    with pytest.raises(ArtifactRejected, match="reviewed"):
        main(["install", "--manifest", str(manifest)], client_factory=forbidden_client)


def test_install_cli_redownloads_exact_reviewed_archive_into_hashed_cache(tmp_path: Path) -> None:
    archive = make_zip(tmp_path, valid_data_entries())
    body = archive.read_bytes()
    reviewed = inspect_archive(archive, maximum_bytes=8 * 1024 * 1024)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(reviewed_manifest_payload(reviewed), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": str(len(body))}, content=body)

    def client_factory() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    cache_root = tmp_path / "cache"
    assert (
        main(
            [
                "install",
                "--manifest",
                str(manifest),
                "--cache-root",
                str(cache_root),
            ],
            client_factory=client_factory,
        )
        == 0
    )
    installed = cache_root / reviewed.sha256
    assert (installed / "tokenizer.json").is_file()
    assert (installed / ".complete.json").is_file()


def test_install_rejects_tampered_manifest_snapshot_hash(tmp_path: Path) -> None:
    archive = make_zip(tmp_path, valid_data_entries())
    reviewed = inspect_archive(archive, maximum_bytes=8 * 1024 * 1024)
    payload = reviewed_manifest_payload(reviewed)
    payload["snapshot_sha256"] = "0" * 64
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    with pytest.raises(ArtifactRejected, match="snapshot"):
        load_reviewed_manifest(manifest)
