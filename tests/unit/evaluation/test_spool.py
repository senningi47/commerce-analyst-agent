import json
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

from commerce_agent.evaluation.spool import (
    SpoolImporter,
    SpoolValidationError,
    SpoolWriter,
)

RUN_SCOPE_DIGEST = "a" * 64
ATTEMPT_ID = uuid4()


def event(sequence: int, *, phase: str = "turn", payload: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "run_scope_digest": RUN_SCOPE_DIGEST,
        "attempt_id": str(ATTEMPT_ID),
        "phase": phase,
        "sequence": sequence,
        "event_type": "agent_event",
        "payload": payload or {"text": "hello"},
    }


def write_spool(path: Path, events: list[dict[str, object]]) -> None:
    writer = SpoolWriter(path)
    for item in events:
        writer.append(item)


def test_writer_appends_one_json_object_per_line(tmp_path: Path) -> None:
    path = tmp_path / "spool.jsonl"
    writer = SpoolWriter(path)
    writer.append(event(0))
    writer.append(event(1))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["sequence"] == 0
    assert json.loads(lines[1])["sequence"] == 1


def test_writer_enforces_size_cap(tmp_path: Path) -> None:
    path = tmp_path / "spool.jsonl"
    writer = SpoolWriter(path, max_bytes=256)
    writer.append(event(0))

    with pytest.raises(SpoolValidationError) as excinfo:
        writer.append(event(1, payload={"text": "x" * 200}))
    assert excinfo.value.reason_code == "spool_size_cap_exceeded"


def test_importer_accepts_and_archives_read_only(tmp_path: Path) -> None:
    spool = tmp_path / "run" / "spool.jsonl"
    write_spool(spool, [event(0), event(1), event(2, phase="submit")])
    original = spool.read_bytes()
    expected_digest = sha256(original).hexdigest()
    archive_dir = tmp_path / "archive"

    receipt = SpoolImporter(archive_dir).validate_and_import(
        spool, run_scope_digest=RUN_SCOPE_DIGEST, attempt_id=ATTEMPT_ID
    )

    assert receipt.event_count == 3
    assert receipt.spool_sha256 == expected_digest
    archive = Path(receipt.archive_path)
    assert archive.exists()
    assert archive.read_bytes() == original
    assert not spool.exists()
    assert not (archive.stat().st_mode & 0o222), "archive must be read-only"


def test_importer_rejects_scope_mismatch(tmp_path: Path) -> None:
    spool = tmp_path / "spool.jsonl"
    write_spool(spool, [event(0)])

    with pytest.raises(SpoolValidationError) as excinfo:
        SpoolImporter(tmp_path / "archive").validate_and_import(
            spool, run_scope_digest="b" * 64, attempt_id=ATTEMPT_ID
        )
    assert excinfo.value.reason_code == "spool_scope_mismatch"


def test_importer_rejects_attempt_mismatch(tmp_path: Path) -> None:
    spool = tmp_path / "spool.jsonl"
    write_spool(spool, [event(0)])

    with pytest.raises(SpoolValidationError) as excinfo:
        SpoolImporter(tmp_path / "archive").validate_and_import(
            spool, run_scope_digest=RUN_SCOPE_DIGEST, attempt_id=uuid4()
        )
    assert excinfo.value.reason_code == "spool_attempt_mismatch"


def test_importer_rejects_sequence_regression_per_phase(tmp_path: Path) -> None:
    spool = tmp_path / "spool.jsonl"
    write_spool(spool, [event(0), event(1), event(0)])

    with pytest.raises(SpoolValidationError) as excinfo:
        SpoolImporter(tmp_path / "archive").validate_and_import(
            spool, run_scope_digest=RUN_SCOPE_DIGEST, attempt_id=ATTEMPT_ID
        )
    assert excinfo.value.reason_code == "spool_sequence_regression"


def test_importer_allows_independent_sequences_across_phases(tmp_path: Path) -> None:
    spool = tmp_path / "spool.jsonl"
    write_spool(spool, [event(0), event(1), event(0, phase="submit")])
    importer = SpoolImporter(tmp_path / "archive")

    receipt = importer.validate_and_import(
        spool, run_scope_digest=RUN_SCOPE_DIGEST, attempt_id=ATTEMPT_ID
    )
    assert receipt.event_count == 3


def test_importer_rejects_forbidden_payload_keys_recursively(tmp_path: Path) -> None:
    spool = tmp_path / "spool.jsonl"
    write_spool(spool, [event(0, payload={"nested": {"sol_sql": "SELECT 1"}})])

    with pytest.raises(SpoolValidationError) as excinfo:
        SpoolImporter(tmp_path / "archive").validate_and_import(
            spool, run_scope_digest=RUN_SCOPE_DIGEST, attempt_id=ATTEMPT_ID
        )
    assert excinfo.value.reason_code == "forbidden_payload_key"


def test_importer_rejects_oversize_file(tmp_path: Path) -> None:
    spool = tmp_path / "spool.jsonl"
    write_spool(spool, [event(0)])
    importer = SpoolImporter(tmp_path / "archive", max_bytes=4)

    with pytest.raises(SpoolValidationError) as excinfo:
        importer.validate_and_import(
            spool, run_scope_digest=RUN_SCOPE_DIGEST, attempt_id=ATTEMPT_ID
        )
    assert excinfo.value.reason_code == "spool_size_cap_exceeded"


def test_importer_rejects_non_json_line(tmp_path: Path) -> None:
    spool = tmp_path / "spool.jsonl"
    writer = SpoolWriter(spool)
    writer.append(event(0))
    with spool.open("a", encoding="utf-8") as handle:
        handle.write("{not json}\n")

    with pytest.raises(SpoolValidationError) as excinfo:
        SpoolImporter(tmp_path / "archive").validate_and_import(
            spool, run_scope_digest=RUN_SCOPE_DIGEST, attempt_id=ATTEMPT_ID
        )
    assert excinfo.value.reason_code == "spool_line_not_json"


def test_importer_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SpoolValidationError) as excinfo:
        SpoolImporter(tmp_path / "archive").validate_and_import(
            tmp_path / "missing.jsonl",
            run_scope_digest=RUN_SCOPE_DIGEST,
            attempt_id=ATTEMPT_ID,
        )
    assert excinfo.value.reason_code == "spool_file_missing"


def test_empty_spool_is_a_valid_zero_event_record(tmp_path: Path) -> None:
    spool = tmp_path / "spool.jsonl"
    SpoolWriter(spool)

    receipt = SpoolImporter(tmp_path / "archive").validate_and_import(
        spool, run_scope_digest=RUN_SCOPE_DIGEST, attempt_id=ATTEMPT_ID
    )

    assert receipt.event_count == 0
    assert Path(receipt.archive_path).exists()
