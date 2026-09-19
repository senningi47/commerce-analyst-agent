"""Provision ignored local Knowledge reader values without printing secrets."""

import os
import re
import secrets
import tempfile
from collections.abc import Callable
from pathlib import Path

_PASSWORD_KEY = "PRODUCT_KNOWLEDGE_READER_PASSWORD"
_DSN_KEY = "PRODUCT_KNOWLEDGE_DATABASE_DSN"


def _parse_env(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def ensure_knowledge_reader_env(
    env_path: Path,
    token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
) -> bool:
    if env_path.name != ".env":
        raise ValueError("knowledge credentials may only be written to .env")
    gitignore_path = env_path.parent / ".gitignore"
    if not gitignore_path.is_file() or ".env" not in {
        line.strip() for line in gitignore_path.read_text(encoding="utf-8").splitlines()
    }:
        raise ValueError("the target .env must be explicitly ignored by Git")

    content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    values = _parse_env(content)
    password_present = bool(values.get(_PASSWORD_KEY))
    dsn_present = bool(values.get(_DSN_KEY))
    keys_declared = _PASSWORD_KEY in values or _DSN_KEY in values
    if password_present and dsn_present:
        return False
    if keys_declared:
        raise ValueError("knowledge credential state is partial or empty")

    token = token_factory()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise ValueError("generated knowledge password is not URL-safe")
    existing_passwords = {
        values.get("PRODUCT_POSTGRES_ADMIN_PASSWORD"),
        values.get("PRODUCT_AGENT_READER_PASSWORD"),
    }
    if token in existing_passwords:
        raise ValueError("knowledge reader password must be distinct from existing roles")

    prefix = content
    if prefix and not prefix.endswith(("\n", "\r")):
        prefix += "\n"
    addition = (
        f"{_PASSWORD_KEY}={token}\n"
        f"{_DSN_KEY}=postgresql://knowledge_reader:{token}"
        "@127.0.0.1:5432/commerce_analyst\n"
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


def main() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    changed = ensure_knowledge_reader_env(env_path)
    if changed:
        print("Knowledge reader environment created")
    else:
        print("Knowledge reader environment already present")


if __name__ == "__main__":
    main()
