import json

import pytest

from scripts.provision_day5_evaluation_env import (
    _DAY5_KEYS,
    ensure_day5_evaluation_env,
    main,
)


def _write_env(path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _gitignored_dir(tmp_path):
    _write_env(tmp_path / ".gitignore", ".env\n")
    return tmp_path


def test_provision_appends_credential_atomically(tmp_path) -> None:
    env_path = _gitignored_dir(tmp_path) / ".env"
    _write_env(env_path, "PRODUCT_POSTGRES_ADMIN_PASSWORD=existing\n")

    changed = ensure_day5_evaluation_env(
        env_path, token_factory=lambda: "generated-token"
    )

    assert changed is True
    content = env_path.read_text(encoding="utf-8")
    assert "PRODUCT_EVALUATION_WRITER_PASSWORD=generated-token" in content
    assert (
        "PRODUCT_EVALUATION_DATABASE_DSN=postgresql://evaluation_writer:"
        "generated-token@127.0.0.1:5432/commerce_analyst?" in content
    )
    assert "application_name=commerce_evaluation_runner" in content
    assert "search_path%3Deval%2Cpg_catalog" in content
    assert list(tmp_path.glob(".env.*")) == []  # no temp residue


def test_provision_is_idempotent(tmp_path) -> None:
    env_path = _gitignored_dir(tmp_path) / ".env"
    ensure_day5_evaluation_env(env_path, token_factory=lambda: "generated-token")

    changed = ensure_day5_evaluation_env(env_path, token_factory=lambda: "other-token")

    assert changed is False
    assert "other-token" not in env_path.read_text(encoding="utf-8")


def test_provision_rejects_partial_state(tmp_path) -> None:
    env_path = _gitignored_dir(tmp_path) / ".env"
    _write_env(env_path, f"{_DAY5_KEYS[0]}=present\n")

    with pytest.raises(ValueError, match="partial or empty"):
        ensure_day5_evaluation_env(env_path, token_factory=lambda: "x")


def test_provision_requires_gitignored_env(tmp_path) -> None:
    env_path = tmp_path / ".env"

    with pytest.raises(ValueError, match="ignored by Git"):
        ensure_day5_evaluation_env(env_path, token_factory=lambda: "x")
    assert not env_path.exists()


def test_provision_rejects_non_env_targets(tmp_path) -> None:
    target = _gitignored_dir(tmp_path) / "other.env"

    with pytest.raises(ValueError, match="may only be written to .env"):
        ensure_day5_evaluation_env(target, token_factory=lambda: "x")


def test_provision_rejects_collision_with_existing_secrets(tmp_path) -> None:
    env_path = _gitignored_dir(tmp_path) / ".env"
    _write_env(env_path, "PRODUCT_POSTGRES_ADMIN_PASSWORD=existing\n")

    with pytest.raises(ValueError, match="distinct from existing"):
        ensure_day5_evaluation_env(env_path, token_factory=lambda: "existing")


def test_main_reports_state_without_printing_secrets(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path = _gitignored_dir(tmp_path) / ".env"
    monkeypatch.chdir(tmp_path)

    main(env_path, token_factory=lambda: "secret-value")

    output = capsys.readouterr().out
    assert "Day 5 evaluation environment created" in output
    assert "secret-value" not in output

    main(env_path, token_factory=lambda: "secret-value")
    assert "already present" in capsys.readouterr().out


def test_day5_key_set_is_frozen() -> None:
    assert _DAY5_KEYS == (
        "PRODUCT_EVALUATION_WRITER_PASSWORD",
        "PRODUCT_EVALUATION_DATABASE_DSN",
    )
    assert json.dumps(_DAY5_KEYS)  # keys are plain serializable names
