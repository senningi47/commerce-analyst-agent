"""Provision the ignored Day 5 evaluation credential without printing secrets."""

import os
import re
import secrets
import tempfile
from collections.abc import Callable
from pathlib import Path

_PASSWORD_SPECS = (
    (
        "PRODUCT_EVALUATION_WRITER_PASSWORD",
        "PRODUCT_EVALUATION_DATABASE_DSN",
        "evaluation_writer",
        "commerce_evaluation_runner",
        "eval,pg_catalog",
    ),
)
_EXISTING_PRODUCT_SECRET_KEYS = (
    "PRODUCT_POSTGRES_ADMIN_PASSWORD",
    "PRODUCT_AGENT_READER_PASSWORD",
    "PRODUCT_KNOWLEDGE_READER_PASSWORD",
    "PRODUCT_CHECKPOINT_WRITER_PASSWORD",
    "PRODUCT_MODEL_STATE_WRITER_PASSWORD",
    "PRODUCT_PROPOSAL_WRITER_PASSWORD",
    "PRODUCT_APPROVAL_WRITER_PASSWORD",
    "PRODUCT_OPERATION_EXECUTOR_PASSWORD",
    "PRODUCT_TRACE_WRITER_PASSWORD",
    "PRODUCT_SCENARIO_RESET_PASSWORD",
)
_DAY5_KEYS = tuple(
    key
    for password_key, dsn_key, _role, _application_name, _search_path in _PASSWORD_SPECS
    for key in (password_key, dsn_key)
)


def _parse_env(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _database_dsn(
    *,
    role: str,
    password: str,
    application_name: str,
    search_path: str,
) -> str:
    encoded_search_path = search_path.replace(",", "%2C")
    return (
        f"postgresql://{role}:{password}@127.0.0.1:5432/commerce_analyst?"
        f"application_name={application_name}&"
        f"options=-csearch_path%3D{encoded_search_path}"
    )


def ensure_day5_evaluation_env(
    env_path: Path,
    token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
) -> bool:
    """Append the Day 5 credential through one same-directory atomic replacement."""

    if env_path.name != ".env":
        raise ValueError("Day 5 evaluation credentials may only be written to .env")
    gitignore_path = env_path.parent / ".gitignore"
    if not gitignore_path.is_file() or ".env" not in {
        line.strip() for line in gitignore_path.read_text(encoding="utf-8").splitlines()
    }:
        raise ValueError("the target .env must be explicitly ignored by Git")

    content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    values = _parse_env(content)
    if all(values.get(key) for key in _DAY5_KEYS):
        return False
    if any(key in values for key in _DAY5_KEYS):
        raise ValueError("Day 5 evaluation credential state is partial or empty")
    generated = [token_factory() for _spec in _PASSWORD_SPECS]
    if not all(re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in generated):
        raise ValueError("generated Day 5 secrets must be URL-safe")
    existing_secrets = {
        values[key] for key in _EXISTING_PRODUCT_SECRET_KEYS if values.get(key)
    }
    if set(generated) & existing_secrets:
        raise ValueError("Day 5 secrets must be distinct from existing Product roles")

    lines: list[str] = []
    for spec, password in zip(_PASSWORD_SPECS, generated, strict=True):
        password_key, dsn_key, role, application_name, search_path = spec
        lines.append(f"{password_key}={password}")
        lines.append(
            f"{dsn_key}="
            + _database_dsn(
                role=role,
                password=password,
                application_name=application_name,
                search_path=search_path,
            )
        )

    prefix = content
    if prefix and not prefix.endswith(("\n", "\r")):
        prefix += "\n"
    addition = "\n".join(lines) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=env_path.parent,
            prefix=".env.",
            delete=False,
        ) as temporary:
            temporary.write(prefix + addition)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, env_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return True


def main(
    env_path: Path | None = None,
    token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
) -> None:
    """Provision the reviewed value and report only the resulting state."""

    target = env_path or Path(__file__).resolve().parents[1] / ".env"
    changed = ensure_day5_evaluation_env(target, token_factory)
    if changed:
        print("Day 5 evaluation environment created")
    else:
        print("Day 5 evaluation environment already present")


if __name__ == "__main__":
    main()
