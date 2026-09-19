"""Provision ignored runtime-state credentials without printing secrets."""

import os
import re
import secrets
import tempfile
from collections.abc import Callable
from pathlib import Path

_CHECKPOINT_PASSWORD_KEY = "PRODUCT_CHECKPOINT_WRITER_PASSWORD"
_CHECKPOINT_DSN_KEY = "PRODUCT_CHECKPOINT_DATABASE_DSN"
_MODEL_STATE_PASSWORD_KEY = "PRODUCT_MODEL_STATE_WRITER_PASSWORD"
_MODEL_STATE_DSN_KEY = "PRODUCT_MODEL_STATE_DATABASE_DSN"
_RUNTIME_STATE_KEYS = (
    _CHECKPOINT_PASSWORD_KEY,
    _CHECKPOINT_DSN_KEY,
    _MODEL_STATE_PASSWORD_KEY,
    _MODEL_STATE_DSN_KEY,
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


def ensure_runtime_state_env(
    env_path: Path,
    token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
) -> bool:
    """Create the four runtime-state values as one atomic file update."""

    if env_path.name != ".env":
        raise ValueError("runtime-state credentials may only be written to .env")
    gitignore_path = env_path.parent / ".gitignore"
    if not gitignore_path.is_file() or ".env" not in {
        line.strip() for line in gitignore_path.read_text(encoding="utf-8").splitlines()
    }:
        raise ValueError("the target .env must be explicitly ignored by Git")

    content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    values = _parse_env(content)
    if all(values.get(key) for key in _RUNTIME_STATE_KEYS):
        return False
    if any(key in values for key in _RUNTIME_STATE_KEYS):
        raise ValueError("runtime-state credential state is partial or empty")

    checkpoint_password = token_factory()
    model_state_password = token_factory()
    for password in (checkpoint_password, model_state_password):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", password):
            raise ValueError("generated runtime-state password is not URL-safe")
    existing_passwords = {
        values.get("PRODUCT_POSTGRES_ADMIN_PASSWORD"),
        values.get("PRODUCT_AGENT_READER_PASSWORD"),
        values.get("PRODUCT_KNOWLEDGE_READER_PASSWORD"),
    }
    if checkpoint_password == model_state_password or {
        checkpoint_password,
        model_state_password,
    } & existing_passwords:
        raise ValueError("runtime-state passwords must be distinct from all product roles")

    prefix = content
    if prefix and not prefix.endswith(("\n", "\r")):
        prefix += "\n"
    addition = (
        f"{_CHECKPOINT_PASSWORD_KEY}={checkpoint_password}\n"
        f"{_CHECKPOINT_DSN_KEY}=postgresql://checkpoint_writer:{checkpoint_password}"
        "@127.0.0.1:5432/commerce_analyst?"
        "application_name=commerce_retail_checkpoint&"
        "options=-csearch_path%3Dcheckpoint%2Cpg_catalog\n"
        f"{_MODEL_STATE_PASSWORD_KEY}={model_state_password}\n"
        f"{_MODEL_STATE_DSN_KEY}=postgresql://model_state_writer:{model_state_password}"
        "@127.0.0.1:5432/commerce_analyst?"
        "application_name=commerce_model_state&"
        "options=-csearch_path%3Dmodel_state%2Cpg_catalog\n"
    )
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
    target = env_path or Path(__file__).resolve().parents[1] / ".env"
    changed = ensure_runtime_state_env(target, token_factory)
    if changed:
        print("Runtime state environment created")
    else:
        print("Runtime state environment already present")


if __name__ == "__main__":
    main()
