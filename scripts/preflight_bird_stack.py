"""Preflight the BIRD evaluation stack before a paid run (pits 67/65).

1. probe the official database (host 5433) until it accepts connections and
   exposes the expected number of user databases — fail-closed on drift;
2. stage `BIRD_EXPERIMENT_ID` into the compose env file the stack is started
   with, so agent-spool records carry the exact experiment identity.

Credentials come from the environment (`BIRD_PG_USER` / `BIRD_PG_PASSWORD`);
run via `uv run --env-file .env ...`. Only sanitized status is ever printed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import psycopg

DEFAULT_COMPOSE_ENV_OUT = Path("outputs/bird-eval/compose-experiment.env")


class MissingCredentials(RuntimeError):
    """BIRD_PG_USER / BIRD_PG_PASSWORD are absent from the environment."""


def default_probe(*, host: str, port: int, user: str, password: str) -> int:
    """One probe round-trip: count non-template databases."""
    with psycopg.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=user,
        connect_timeout=5,
    ) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_database WHERE NOT datistemplate")
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def run_preflight(
    *,
    probe: object,
    experiment: str,
    expect_databases: int,
    attempts: int,
    compose_env_out: Path,
    repeat_seconds: float = 2.0,
) -> tuple[int, str]:
    """Probe with retries, then stage the compose env file.

    Returns (exit_code, sanitized status message). The staged env file is only
    written once the database is verified ready.
    """
    user = os.environ.get("BIRD_PG_USER")
    password = os.environ.get("BIRD_PG_PASSWORD")
    if not user or not password:
        raise MissingCredentials("BIRD_PG_USER/BIRD_PG_PASSWORD must be set")

    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        try:
            count = int(probe())  # type: ignore[operator]
        except Exception as error:  # noqa: BLE001 - sanitized retry classification
            last_error = type(error).__name__
            if attempt < attempts:
                time.sleep(repeat_seconds)
            continue
        if count != expect_databases:
            return (
                1,
                f"database_count_mismatch: observed {count}, expected {expect_databases}",
            )
        compose_env_out.parent.mkdir(parents=True, exist_ok=True)
        compose_env_out.write_text(f"BIRD_EXPERIMENT_ID={experiment}\n", encoding="utf-8")
        return (
            0,
            (
                f"official db ready ({count} databases) after {attempt} probe(s); "
                f"compose env staged: {compose_env_out}"
            ),
        )
    return (1, f"official_db_unreachable: last probe error class {last_error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BIRD stack preflight (pits 67/65)")
    parser.add_argument("--experiment", required=True)
    parser.add_argument(
        "--expect-databases",
        type=int,
        default=25,
        help="non-template baseline: 22 official domains + postgres/root/sql_test_template",
    )
    parser.add_argument("--attempts", type=int, default=60)
    parser.add_argument("--repeat-seconds", type=float, default=2.0)
    parser.add_argument("--compose-env-out", type=Path, default=DEFAULT_COMPOSE_ENV_OUT)
    args = parser.parse_args(argv)

    exit_code, message = run_preflight(
        probe=lambda: default_probe(
            host="127.0.0.1",
            port=5433,
            user=os.environ.get("BIRD_PG_USER", ""),
            password=os.environ.get("BIRD_PG_PASSWORD", ""),
        ),
        experiment=args.experiment,
        expect_databases=args.expect_databases,
        attempts=args.attempts,
        compose_env_out=args.compose_env_out,
        repeat_seconds=args.repeat_seconds,
    )
    print(message)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
