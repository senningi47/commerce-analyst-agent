import json
from pathlib import Path
from typing import Any, Self

import pytest

from commerce_agent.knowledge.errors import KnowledgeCatalogInvalid
from scripts.import_knowledge import import_catalog


class RecordingCursor:
    def __init__(
        self,
        *,
        content_match: tuple[str] | None = None,
        revision_match: tuple[str] | None = None,
        fail_alias_insert: bool = False,
    ) -> None:
        self.content_match = content_match
        self.revision_match = revision_match
        self.fail_alias_insert = fail_alias_insert
        self.current: tuple[Any, ...] | None = None
        self.events: list[str] = []
        self.document_count = 0
        self.alias_count = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, params: tuple[Any, ...] | None = None) -> None:
        compact = " ".join(statement.split())
        if "WHERE content_sha256 = %s" in compact:
            self.events.append("check_content")
            self.current = self.content_match
        elif compact.startswith("SELECT count(*) FROM knowledge.document"):
            self.events.append("count_documents")
            self.current = (self.document_count,)
        elif compact.startswith("SELECT count(*) FROM knowledge.value_alias"):
            self.events.append("count_aliases")
            self.current = (self.alias_count,)
        elif compact.startswith("UPDATE knowledge.catalog_revision SET active = false"):
            self.events.append("deactivate")
            self.current = None
        elif compact.startswith("UPDATE knowledge.catalog_revision SET active = true"):
            self.events.append("activate")
            self.current = None
        elif "WHERE revision_id = %s" in compact:
            self.events.append("check_revision")
            self.current = self.revision_match
        elif compact.startswith("SELECT DISTINCT"):
            self.events.append("verify_aliases")
            self.current = None
            self._rows = [(value,) for value in params[0]]
        elif compact.startswith("INSERT INTO knowledge.catalog_revision"):
            self.events.append("insert_revision")
            self.current = None
        else:
            self.events.append("other")
            self.current = None

    def executemany(self, statement: str, params: list[tuple[Any, ...]]) -> None:
        compact = " ".join(statement.split())
        if "knowledge.document" in compact:
            self.events.append("insert_documents")
            self.document_count = len(params)
        elif "knowledge.value_alias" in compact:
            self.events.append("insert_aliases")
            if self.fail_alias_insert:
                raise RuntimeError("injected alias failure")
            self.alias_count = len(params)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.current

    def fetchall(self) -> list[tuple[Any, ...]]:
        return getattr(self, "_rows", [])


class RecordingConnection:
    def __init__(self, cursor: RecordingCursor) -> None:
        self.recording_cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def cursor(self) -> RecordingCursor:
        return self.recording_cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1


def catalog_path(tmp_path: Path) -> Path:
    payload = {
        "schema_version": 1,
        "revision_id": "retail-catalog-v1",
        "data_manifest_sha256": "a" * 64,
        "documents": [
            {
                "doc_id": "table.products",
                "kind": "table",
                "title": "Products",
                "content": {"columns": ["product_category_name"]},
                "source_type": "test",
                "source_path": "source.py",
                "source_sha256": "b" * 64,
                "allowed_profiles": ["retail"],
            },
            {
                "doc_id": "metric.gmv",
                "kind": "metric",
                "title": "GMV",
                "content": {"status": "clarification_required"},
                "source_type": "test",
                "source_path": "spec.md",
                "source_sha256": "c" * 64,
                "allowed_profiles": ["retail"],
            },
        ],
        "aliases": [
            {
                "domain": "product_category",
                "normalized_alias": "health_beauty",
                "canonical_value": "beleza_saude",
                "display_label": "Beleza e saúde",
                "locale": "en",
                "source_doc_id": "table.products",
            }
        ],
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_same_content_hash_returns_no_op_before_inserts(tmp_path: Path) -> None:
    cursor = RecordingCursor(content_match=("retail-catalog-v1",))
    connection = RecordingConnection(cursor)

    outcome = import_catalog("admin-dsn", catalog_path(tmp_path), connect=lambda **_: connection)

    assert outcome.status == "no_op"
    assert cursor.events == ["check_content"]
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closes == 1


def test_same_revision_with_different_hash_is_rejected(tmp_path: Path) -> None:
    cursor = RecordingCursor(revision_match=("f" * 64,))
    connection = RecordingConnection(cursor)

    with pytest.raises(KnowledgeCatalogInvalid) as caught:
        import_catalog("admin-dsn", catalog_path(tmp_path), connect=lambda **_: connection)

    assert caught.value.reason_code == "revision_conflict"
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_successful_import_orders_writes_and_activation(tmp_path: Path) -> None:
    cursor = RecordingCursor()
    connection = RecordingConnection(cursor)

    outcome = import_catalog("admin-dsn", catalog_path(tmp_path), connect=lambda **_: connection)

    assert outcome.status == "inserted"
    assert cursor.events.index("insert_revision") < cursor.events.index("insert_documents")
    assert cursor.events.index("insert_documents") < cursor.events.index("insert_aliases")
    assert cursor.events.index("insert_aliases") < cursor.events.index("count_documents")
    assert cursor.events.index("count_aliases") < cursor.events.index("deactivate")
    assert cursor.events.index("deactivate") < cursor.events.index("activate")
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert "beleza_saude" not in repr(outcome)


def test_import_failure_rolls_back_and_never_commits(tmp_path: Path) -> None:
    cursor = RecordingCursor(fail_alias_insert=True)
    connection = RecordingConnection(cursor)

    with pytest.raises(RuntimeError, match="injected"):
        import_catalog("admin-dsn", catalog_path(tmp_path), connect=lambda **_: connection)

    assert connection.rollbacks == 1
    assert connection.commits == 0
    assert connection.closes == 1
