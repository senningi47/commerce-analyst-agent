from collections.abc import Iterator
from pathlib import Path

import pytest

from scripts.provision_runtime_state_env import ensure_runtime_state_env, main


def repo_env(tmp_path: Path, content: str) -> Path:
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text(content, encoding="utf-8")
    return env_path


def token_factory(tokens: Iterator[str]):
    return lambda: next(tokens)


def test_missing_runtime_state_values_are_created_atomically_without_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = repo_env(
        tmp_path,
        "PRODUCT_POSTGRES_ADMIN_PASSWORD=admin\n"
        "PRODUCT_AGENT_READER_PASSWORD=agent\n"
        "PRODUCT_KNOWLEDGE_READER_PASSWORD=knowledge\n",
    )
    replacements: list[tuple[Path, Path]] = []
    real_replace = __import__("os").replace

    def recording_replace(source: Path, target: Path) -> None:
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr("scripts.provision_runtime_state_env.os.replace", recording_replace)

    changed = ensure_runtime_state_env(
        env_path,
        token_factory(iter(("checkpoint-token", "model-token"))),
    )

    content = env_path.read_text(encoding="utf-8")
    assert changed is True
    assert len(replacements) == 1
    assert replacements[0][1] == env_path
    assert "PRODUCT_CHECKPOINT_WRITER_PASSWORD=checkpoint-token" in content
    assert (
        "PRODUCT_CHECKPOINT_DATABASE_DSN="
        "postgresql://checkpoint_writer:checkpoint-token@127.0.0.1:5432/"
        "commerce_analyst?application_name=commerce_retail_checkpoint&"
        "options=-csearch_path%3Dcheckpoint%2Cpg_catalog"
    ) in content
    assert "PRODUCT_MODEL_STATE_WRITER_PASSWORD=model-token" in content
    assert (
        "PRODUCT_MODEL_STATE_DATABASE_DSN="
        "postgresql://model_state_writer:model-token@127.0.0.1:5432/"
        "commerce_analyst?application_name=commerce_model_state&"
        "options=-csearch_path%3Dmodel_state%2Cpg_catalog"
    ) in content
    assert capsys.readouterr().out == ""


def test_complete_runtime_state_values_are_not_rotated(tmp_path: Path) -> None:
    original = (
        "PRODUCT_CHECKPOINT_WRITER_PASSWORD=checkpoint\n"
        "PRODUCT_CHECKPOINT_DATABASE_DSN=postgresql://checkpoint_writer:checkpoint@db/name\n"
        "PRODUCT_MODEL_STATE_WRITER_PASSWORD=model\n"
        "PRODUCT_MODEL_STATE_DATABASE_DSN=postgresql://model_state_writer:model@db/name\n"
    )
    env_path = repo_env(tmp_path, original)

    changed = ensure_runtime_state_env(
        env_path,
        token_factory(iter(("replacement-a", "replacement-b"))),
    )

    assert changed is False
    assert env_path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "content",
    [
        "PRODUCT_CHECKPOINT_WRITER_PASSWORD=checkpoint\n",
        "PRODUCT_MODEL_STATE_DATABASE_DSN=postgresql://model_state_writer:x@db/name\n",
        (
            "PRODUCT_CHECKPOINT_WRITER_PASSWORD=\n"
            "PRODUCT_CHECKPOINT_DATABASE_DSN=\n"
            "PRODUCT_MODEL_STATE_WRITER_PASSWORD=\n"
            "PRODUCT_MODEL_STATE_DATABASE_DSN=\n"
        ),
    ],
)
def test_partial_or_empty_runtime_state_group_fails_without_modification(
    tmp_path: Path,
    content: str,
) -> None:
    env_path = repo_env(tmp_path, content)

    with pytest.raises(ValueError, match="partial"):
        ensure_runtime_state_env(
            env_path,
            token_factory(iter(("replacement-a", "replacement-b"))),
        )

    assert env_path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize(
    "tokens",
    [
        ("same", "same"),
        ("admin", "new-model"),
        ("new-checkpoint", "agent"),
        ("knowledge", "new-model"),
    ],
)
def test_generated_passwords_are_pairwise_distinct_from_product_roles(
    tmp_path: Path,
    tokens: tuple[str, str],
) -> None:
    original = (
        "PRODUCT_POSTGRES_ADMIN_PASSWORD=admin\n"
        "PRODUCT_AGENT_READER_PASSWORD=agent\n"
        "PRODUCT_KNOWLEDGE_READER_PASSWORD=knowledge\n"
    )
    env_path = repo_env(tmp_path, original)

    with pytest.raises(ValueError, match="distinct"):
        ensure_runtime_state_env(env_path, token_factory(iter(tokens)))

    assert env_path.read_text(encoding="utf-8") == original


def test_runtime_state_provisioner_requires_exact_ignored_dot_env(tmp_path: Path) -> None:
    wrong_name = tmp_path / "secrets.env"
    wrong_name.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match=r"\.env"):
        ensure_runtime_state_env(
            wrong_name,
            token_factory(iter(("checkpoint", "model"))),
        )

    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="ignored"):
        ensure_runtime_state_env(
            env_path,
            token_factory(iter(("checkpoint", "model"))),
        )


def test_cli_prints_only_created_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_path = repo_env(tmp_path, "")

    main(
        env_path,
        token_factory(iter(("checkpoint-token", "model-token"))),
    )

    assert capsys.readouterr().out == "Runtime state environment created\n"


def test_cli_prints_only_already_present_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_path = repo_env(
        tmp_path,
        "PRODUCT_CHECKPOINT_WRITER_PASSWORD=checkpoint\n"
        "PRODUCT_CHECKPOINT_DATABASE_DSN=postgresql://checkpoint_writer:checkpoint@db/name\n"
        "PRODUCT_MODEL_STATE_WRITER_PASSWORD=model\n"
        "PRODUCT_MODEL_STATE_DATABASE_DSN=postgresql://model_state_writer:model@db/name\n",
    )

    main(
        env_path,
        token_factory(iter(("replacement-a", "replacement-b"))),
    )

    assert capsys.readouterr().out == "Runtime state environment already present\n"
