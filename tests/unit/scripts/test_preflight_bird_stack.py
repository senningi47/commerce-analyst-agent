"""Unit tests for the BIRD stack preflight script (pits 67/65)."""

import os
from pathlib import Path
from typing import Self

import pytest

from scripts.preflight_bird_stack import (
    MissingCredentials,
    run_preflight,
)


@pytest.fixture
def pg_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIRD_PG_USER", "probe-user")
    monkeypatch.setenv("BIRD_PG_PASSWORD", "probe-password")


def _fake_probe(calls: list[str], outputs: list[int]) -> object:
    def probe() -> int:
        calls.append("probe")
        result = outputs.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    return probe


def test_probe_retry_then_success_stages_experiment_env(
    tmp_path: Path, pg_env: None
) -> None:
    """Pit 67: the official DB may refuse connections during the stack's
    cold-start window — the preflight retries until it is ready and stages
    BIRD_EXPERIMENT_ID for the compose env (pit 65)."""
    import psycopg

    calls: list[str] = []
    outputs: list[int | Exception] = [
        psycopg.OperationalError("connection refused"),
        25,
    ]
    env_out = tmp_path / "compose-experiment.env"

    exit_code, message = run_preflight(
        probe=_fake_probe(calls, outputs),
        experiment="task7-cmode-refit-20260915i",
        expect_databases=25,
        attempts=3,
        compose_env_out=env_out,
    )

    assert exit_code == 0
    assert calls == ["probe", "probe"]
    assert "ready" in message
    assert "probe-password" not in message
    assert env_out.read_text(encoding="utf-8") == (
        "BIRD_EXPERIMENT_ID=task7-cmode-refit-20260915i\n"
    )


def test_database_count_drift_fails_closed(tmp_path: Path, pg_env: None) -> None:
    calls: list[str] = []
    outputs: list[int | Exception] = [18]
    env_out = tmp_path / "compose-experiment.env"

    exit_code, message = run_preflight(
        probe=_fake_probe(calls, outputs),
        experiment="exp",
        expect_databases=22,
        attempts=1,
        compose_env_out=env_out,
    )

    assert exit_code == 1
    assert "database_count_mismatch" in message
    assert not env_out.exists()


def test_exhausted_retries_fail_closed(tmp_path: Path, pg_env: None) -> None:
    import psycopg

    calls: list[str] = []
    outputs: list[int | Exception] = [
        psycopg.OperationalError("no"),
        psycopg.OperationalError("no"),
    ]
    env_out = tmp_path / "compose-experiment.env"

    exit_code, message = run_preflight(
        probe=_fake_probe(calls, outputs),
        experiment="exp",
        expect_databases=22,
        attempts=2,
        compose_env_out=env_out,
    )

    assert exit_code == 1
    assert "official_db_unreachable" in message
    assert not env_out.exists()


def test_missing_credentials_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BIRD_PG_USER", raising=False)
    monkeypatch.delenv("BIRD_PG_PASSWORD", raising=False)

    with pytest.raises(MissingCredentials):
        run_preflight(
            probe=lambda: 25,
            experiment="exp",
            expect_databases=22,
            attempts=1,
            compose_env_out=tmp_path / "env",
        )


def test_default_probe_counts_nontemplate_databases(
    tmp_path: Path, pg_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real probe connects with the BIRD_PG_* credentials and counts
    non-template databases; credentials are never echoed."""
    seen: dict[str, object] = {}

    class FakeCursor:
        def execute(self, query: str) -> None:
            seen["query"] = query

        def fetchone(self) -> tuple[int]:
            return (25,)

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    class FakeConn:
        def __init__(self, **kwargs: object) -> None:
            seen.update(kwargs)

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            seen["closed"] = True

        def cursor(self) -> FakeCursor:
            return FakeCursor()

    monkeypatch.setattr("scripts.preflight_bird_stack.psycopg.connect", FakeConn)
    from scripts.preflight_bird_stack import default_probe

    count = default_probe(
        host="127.0.0.1",
        port=5433,
        user=os.environ["BIRD_PG_USER"],
        password=os.environ["BIRD_PG_PASSWORD"],
    )

    assert count == 25
    assert seen["query"] == "SELECT count(*) FROM pg_database WHERE NOT datistemplate"
    assert seen["closed"] is True
