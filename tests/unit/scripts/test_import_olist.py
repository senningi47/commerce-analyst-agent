from pathlib import Path

import pytest

from scripts.import_olist import (
    _normalize_review_blanks,
    sha256_file,
    verify_empty_targets,
)


def test_sha256_file_hashes_exact_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_bytes(b"header\nvalue\n")

    assert sha256_file(source) == "2438de117fe81b671bd30d925d93f6883c660182dd44def976c1bdf63680022d"


def test_verify_empty_targets_rejects_existing_rows() -> None:
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        verify_empty_targets({"customers": 1, "orders": 0})


def test_normalize_review_blanks_preserves_non_blank_text() -> None:
    class RecordingCursor:
        statement: str | None = None

        def execute(self, statement: str) -> None:
            self.statement = statement

    cursor = RecordingCursor()

    _normalize_review_blanks(cursor)

    assert cursor.statement is not None
    assert "THEN NULL ELSE review_comment_title END" in cursor.statement
    assert "THEN NULL ELSE review_comment_message END" in cursor.statement
    assert "review_comment_title ~ '^[[:space:]]*$'" in cursor.statement
    assert "review_comment_message ~ '^[[:space:]]*$'" in cursor.statement
