"""Inspect and install a reviewed, data-only DeepSeek tokenizer artifact."""

import argparse
import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from stat import S_ISDIR, S_ISREG
from typing import Literal
from urllib.parse import urlsplit
from zipfile import ZipFile

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MAX_COMPRESSION_RATIO = 100
MAX_EXTRACTED_BYTES = 16 * 1024 * 1024
APPROVED_URL = "https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip"
DATA_SUFFIXES = frozenset({".json", ".model", ".tiktoken", ".txt"})
KNOWN_VENDOR_DEMO = "deepseek_v4_tokenizer/deepseek_tokenizer.py"


class ArtifactRejected(ValueError):
    """The tokenizer artifact failed a fail-closed security check."""


class ArtifactEntry(BaseModel, frozen=True, extra="forbid"):
    name: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0, le=MAX_EXTRACTED_BYTES)


class ArtifactInspection(BaseModel, frozen=True, extra="forbid"):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: tuple[ArtifactEntry, ...] = Field(min_length=1, max_length=64)
    directories: tuple[str, ...] = Field(default=(), max_length=64)
    excluded_entries: tuple[ArtifactEntry, ...] = Field(default=(), max_length=1)


class ReviewedArtifactManifest(BaseModel, frozen=True, extra="forbid"):
    schema_name: Literal["commerce-agent.tokenizer-artifact.v1"] = Field(alias="schema")
    estimator_revision: str = Field(min_length=1, max_length=128)
    renderer_revision: Literal["deepseek-chat-renderer-v1"]
    url: Literal["https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip"]
    review_status: Literal["reviewed"]
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: tuple[ArtifactEntry, ...] = Field(min_length=1, max_length=64)
    directories: tuple[str, ...] = Field(default=(), max_length=64)
    excluded_entries: tuple[ArtifactEntry, ...] = Field(default=(), max_length=1)
    reviewed_at: datetime
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def validate_reviewed_at(self) -> "ReviewedArtifactManifest":
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
            raise ValueError("reviewed_at must be timezone-aware")
        return self


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--candidate-output", type=Path, required=True)
    install = commands.add_parser("install")
    install.add_argument("--manifest", type=Path, required=True)
    install.add_argument(
        "--cache-root",
        type=Path,
        default=Path(".cache/commerce-agent/deepseek-tokenizer"),
    )
    return parser


def validate_source_url(url: str) -> None:
    """Reject every source other than the reviewed HTTPS tokenizer URL."""
    if url != APPROVED_URL:
        raise ArtifactRejected("tokenizer URL is not allowlisted")


def validate_download_metadata(
    *, final_url: str, redirect_chain: tuple[str, ...], content_length: int | None, maximum_bytes: int
) -> None:
    """Validate redirects and declared response size before streaming a body."""
    for url in (*redirect_chain, final_url):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "cdn.deepseek.com":
            raise ArtifactRejected("download redirect left the HTTPS host allowlist")
    if content_length is not None and content_length > maximum_bytes:
        raise ArtifactRejected("download content length exceeds maximum bytes")


def download_and_inspect(
    client: httpx.Client,
    *,
    candidate_output: Path,
    inspected_at: datetime,
    maximum_bytes: int = 8 * 1024 * 1024,
) -> ArtifactInspection:
    """Download to a temporary file and persist inspection metadata only."""
    validate_source_url(APPROVED_URL)
    if inspected_at.tzinfo is None or inspected_at.utcoffset() is None:
        raise ValueError("inspected_at must be timezone-aware")
    temporary_path: Path | None = None
    try:
        with client.stream("GET", APPROVED_URL, follow_redirects=True) as response:
            response.raise_for_status()
            history = tuple(str(item.url) for item in response.history)
            declared = response.headers.get("content-length")
            content_length = int(declared) if declared is not None else None
            validate_download_metadata(
                final_url=str(response.url),
                redirect_chain=history,
                content_length=content_length,
                maximum_bytes=maximum_bytes,
            )
            downloaded = 0
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as output:
                temporary_path = Path(output.name)
                for chunk in response.iter_bytes(64 * 1024):
                    downloaded += len(chunk)
                    if downloaded > maximum_bytes:
                        raise ArtifactRejected("download body exceeds maximum bytes")
                    output.write(chunk)
        inspection = inspect_archive(temporary_path, maximum_bytes=maximum_bytes)
        candidate = {
            "archive_sha256": inspection.sha256,
            "content_length": downloaded,
            "directories": list(inspection.directories),
            "entries": [entry.model_dump(mode="json") for entry in inspection.entries],
            "excluded_entries": [
                entry.model_dump(mode="json") for entry in inspection.excluded_entries
            ],
            "final_url": str(response.url),
            "inspected_at": inspected_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "last_modified": response.headers.get("last-modified"),
            "redirect_chain": list(history),
            "regular_files": [entry.name for entry in inspection.entries],
            "url": APPROVED_URL,
        }
        candidate_output.parent.mkdir(parents=True, exist_ok=True)
        with candidate_output.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(candidate, output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return inspection
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def load_reviewed_manifest(path: Path) -> ArtifactInspection:
    """Load only the reviewed archive identity and exact regular-file set."""
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError("manifest must be an object")
        validate_source_url(str(payload.get("url", "")))
        if payload.get("review_status") != "reviewed":
            raise ArtifactRejected("tokenizer manifest has not been reviewed")
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        if raw.strip() != canonical:
            raise ArtifactRejected("reviewed tokenizer manifest must use canonical JSON")
        body = {key: value for key, value in payload.items() if key != "snapshot_sha256"}
        expected = sha256(
            json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        if payload.get("snapshot_sha256") != expected:
            raise ArtifactRejected("reviewed tokenizer manifest snapshot sha256 mismatch")
        reviewed = ReviewedArtifactManifest.model_validate(payload)
        return ArtifactInspection(
            sha256=reviewed.archive_sha256,
            entries=reviewed.entries,
            directories=reviewed.directories,
            excluded_entries=reviewed.excluded_entries,
        )
    except ArtifactRejected:
        raise
    except (OSError, json.JSONDecodeError, TypeError, ValidationError) as error:
        raise ArtifactRejected("reviewed tokenizer manifest is invalid") from error


def main(
    argv: list[str] | None = None,
    *,
    client_factory: Callable[[], httpx.Client] = httpx.Client,
) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "install":
        reviewed = load_reviewed_manifest(args.manifest)
        temporary_path: Path | None = None
        try:
            with client_factory() as client, client.stream(
                "GET", APPROVED_URL, follow_redirects=True
            ) as response:
                response.raise_for_status()
                declared = response.headers.get("content-length")
                validate_download_metadata(
                    final_url=str(response.url),
                    redirect_chain=tuple(str(item.url) for item in response.history),
                    content_length=int(declared) if declared is not None else None,
                    maximum_bytes=8 * 1024 * 1024,
                )
                downloaded = 0
                with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as output:
                    temporary_path = Path(output.name)
                    for chunk in response.iter_bytes(64 * 1024):
                        downloaded += len(chunk)
                        if downloaded > 8 * 1024 * 1024:
                            raise ArtifactRejected("download body exceeds maximum bytes")
                        output.write(chunk)
            install_archive(temporary_path, reviewed, args.cache_root / reviewed.sha256)
            return 0
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    with client_factory() as client:
        download_and_inspect(
            client,
            candidate_output=args.candidate_output,
            inspected_at=datetime.now(UTC),
        )
    return 0


def inspect_archive(archive: Path, *, maximum_bytes: int) -> ArtifactInspection:
    """Inspect a local ZIP without extracting it."""
    if archive.stat().st_size > maximum_bytes:
        raise ArtifactRejected("archive exceeds maximum bytes")
    entries: list[ArtifactEntry] = []
    directories: list[str] = []
    excluded_entries: list[ArtifactEntry] = []
    digest = sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    with ZipFile(archive) as bundle:
        if len(bundle.infolist()) > 64:
            raise ArtifactRejected("archive cannot contain more than 64 entries")
        seen_names: set[str] = set()
        extracted_bytes = 0
        for info in bundle.infolist():
            name = info.filename.replace("\\", "/")
            folded_name = name.casefold()
            if folded_name in seen_names:
                raise ArtifactRejected("duplicate or case-colliding archive entry")
            seen_names.add(folded_name)
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise ArtifactRejected("unsafe or non-data archive entry")
            is_vendor_demo = name == KNOWN_VENDOR_DEMO
            if not info.is_dir() and path.suffix.lower() not in DATA_SUFFIXES and not is_vendor_demo:
                raise ArtifactRejected("unsafe or non-data archive entry")
            if info.create_system == 3:
                mode = info.external_attr >> 16
                if mode and not (S_ISREG(mode) or S_ISDIR(mode)):
                    raise ArtifactRejected("archive entries must be regular files or directories")
            if not info.is_dir():
                extracted_bytes += info.file_size
                if extracted_bytes > MAX_EXTRACTED_BYTES:
                    raise ArtifactRejected("archive exceeds total extracted byte limit")
                if info.file_size > max(1, info.compress_size) * MAX_COMPRESSION_RATIO:
                    raise ArtifactRejected("archive entry exceeds compression ratio limit")
                content = bundle.read(info)
                entry = ArtifactEntry(
                    name=name, sha256=sha256(content).hexdigest(), size=len(content)
                )
                (excluded_entries if is_vendor_demo else entries).append(entry)
            else:
                directories.append(name)
    if not entries:
        raise ArtifactRejected("archive contains no regular data files")
    return ArtifactInspection(
        sha256=digest.hexdigest(),
        entries=tuple(entries),
        directories=tuple(directories),
        excluded_entries=tuple(excluded_entries),
    )


def install_archive(archive: Path, manifest: ArtifactInspection, target: Path) -> Path:
    """Install only an archive exactly matching a reviewed inspection."""
    actual = inspect_archive(archive, maximum_bytes=8 * 1024 * 1024)
    if actual.sha256 != manifest.sha256:
        raise ArtifactRejected("archive sha256 does not match reviewed manifest")
    if (
        actual.entries != manifest.entries
        or actual.directories != manifest.directories
        or actual.excluded_entries != manifest.excluded_entries
    ):
        raise ArtifactRejected("archive entry set does not match reviewed manifest")
    target.mkdir(parents=True, exist_ok=False)
    with ZipFile(archive) as bundle:
        for entry in manifest.entries:
            destination = target / entry.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as output:
                output.write(bundle.read(entry.name))
    marker = target / ".complete.json"
    temporary_marker = target / ".complete.tmp"
    with temporary_marker.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(
            {"archive_sha256": manifest.sha256, "entries": len(manifest.entries)},
            output,
            sort_keys=True,
            separators=(",", ":"),
        )
    os.replace(temporary_marker, marker)
    return target


if __name__ == "__main__":
    raise SystemExit(main())
