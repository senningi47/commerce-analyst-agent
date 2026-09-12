from pathlib import Path

import pytest

from scripts.provision_knowledge_reader_env import ensure_knowledge_reader_env


def repo_env(tmp_path: Path, content: str) -> Path:
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text(content, encoding="utf-8")
    return env_path


def test_missing_values_are_created_without_printing_token(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_path = repo_env(
        tmp_path,
        "COMMERCE_AGENT_ENVIRONMENT=development\n"
        "PRODUCT_POSTGRES_ADMIN_PASSWORD=admin\n"
        "PRODUCT_AGENT_READER_PASSWORD=agent\n",
    )

    changed = ensure_knowledge_reader_env(env_path, lambda: "generated-token")

    content = env_path.read_text(encoding="utf-8")
    assert changed is True
    assert "PRODUCT_KNOWLEDGE_READER_PASSWORD=generated-token" in content
    assert (
        "PRODUCT_KNOWLEDGE_DATABASE_DSN="
        "postgresql://knowledge_reader:generated-token@127.0.0.1:5432/commerce_analyst"
    ) in content
    assert "generated-token" not in capsys.readouterr().out


def test_provisioner_does_not_rotate_existing_values(tmp_path: Path) -> None:
    env_path = repo_env(
        tmp_path,
        "PRODUCT_KNOWLEDGE_READER_PASSWORD=existing\n"
        "PRODUCT_KNOWLEDGE_DATABASE_DSN="
        "postgresql://knowledge_reader:existing@127.0.0.1:5432/commerce_analyst\n",
    )

    changed = ensure_knowledge_reader_env(env_path, lambda: "replacement")

    assert changed is False
    assert "replacement" not in env_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "content",
    [
        "PRODUCT_KNOWLEDGE_READER_PASSWORD=existing\n",
        "PRODUCT_KNOWLEDGE_DATABASE_DSN=postgresql://knowledge_reader:x@db/name\n",
        "PRODUCT_KNOWLEDGE_READER_PASSWORD=\nPRODUCT_KNOWLEDGE_DATABASE_DSN=\n",
    ],
)
def test_partial_or_empty_state_fails_closed(tmp_path: Path, content: str) -> None:
    env_path = repo_env(tmp_path, content)

    with pytest.raises(ValueError, match="partial"):
        ensure_knowledge_reader_env(env_path, lambda: "replacement")


@pytest.mark.parametrize(
    "existing_key", ["PRODUCT_POSTGRES_ADMIN_PASSWORD", "PRODUCT_AGENT_READER_PASSWORD"]
)
def test_generated_password_must_differ_from_existing_roles(
    tmp_path: Path,
    existing_key: str,
) -> None:
    env_path = repo_env(tmp_path, f"{existing_key}=collision\n")

    with pytest.raises(ValueError, match="distinct"):
        ensure_knowledge_reader_env(env_path, lambda: "collision")


def test_provisioner_requires_ignored_dot_env_target(tmp_path: Path) -> None:
    wrong_name = tmp_path / "secrets.env"
    wrong_name.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match=r"\.env"):
        ensure_knowledge_reader_env(wrong_name, lambda: "token")

    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="ignored"):
        ensure_knowledge_reader_env(env_path, lambda: "token")
