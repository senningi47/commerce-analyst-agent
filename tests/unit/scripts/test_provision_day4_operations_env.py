from collections.abc import Iterator
from pathlib import Path

import pytest

from scripts.provision_day4_operations_env import ensure_day4_operations_env, main

DAY4_KEYS = (
    "PRODUCT_PROPOSAL_WRITER_PASSWORD",
    "PRODUCT_PROPOSAL_DATABASE_DSN",
    "PRODUCT_APPROVAL_WRITER_PASSWORD",
    "PRODUCT_APPROVAL_DATABASE_DSN",
    "PRODUCT_OPERATION_EXECUTOR_PASSWORD",
    "PRODUCT_OPERATION_DATABASE_DSN",
    "PRODUCT_TRACE_WRITER_PASSWORD",
    "PRODUCT_TRACE_DATABASE_DSN",
    "PRODUCT_SCENARIO_RESET_PASSWORD",
    "PRODUCT_SCENARIO_RESET_DATABASE_DSN",
    "PRODUCT_OPERATION_APPROVAL_HMAC_KEY_V1",
    "PRODUCT_SELLER_REF_HMAC_KEY_V1",
)


def _env_file(tmp_path: Path, content: str = "") -> Path:
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text(content, encoding="utf-8")
    return env_path


def _factory(values: Iterator[str]):
    return lambda: next(values)


def _parse_day4_values(content: str) -> dict[str, str]:
    parsed = dict(
        line.split("=", 1)
        for line in content.splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    return {key: parsed[key] for key in DAY4_KEYS}


def test_provisioner_writes_distinct_values_atomically_without_printing_them(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = _env_file(tmp_path)
    generated = iter(f"generated-{index}" for index in range(1, 8))
    replacements: list[tuple[Path, Path]] = []
    real_replace = __import__("os").replace

    def recording_replace(source: Path, target: Path) -> None:
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(
        "scripts.provision_day4_operations_env.os.replace",
        recording_replace,
    )

    changed = ensure_day4_operations_env(
        env_path,
        token_factory=_factory(generated),
        key_factory=_factory(generated),
    )

    values = _parse_day4_values(env_path.read_text(encoding="utf-8"))
    assert changed is True
    assert len(values) == 12
    assert len(set(values.values())) == 12
    assert replacements and len(replacements) == 1
    assert replacements[0][0].parent == env_path.parent
    assert replacements[0][1] == env_path
    assert values["PRODUCT_PROPOSAL_DATABASE_DSN"] == (
        "postgresql://proposal_writer:generated-1@127.0.0.1:5432/commerce_analyst?"
        "application_name=commerce_operation_proposal&"
        "options=-csearch_path%3Dops%2Cretail%2Ctrusted_schema%2Cpg_catalog"
    )
    assert values["PRODUCT_APPROVAL_DATABASE_DSN"] == (
        "postgresql://approval_writer:generated-2@127.0.0.1:5432/commerce_analyst?"
        "application_name=commerce_operation_approval&"
        "options=-csearch_path%3Dops%2Ctrusted_schema%2Cpg_catalog"
    )
    assert values["PRODUCT_OPERATION_DATABASE_DSN"] == (
        "postgresql://operation_executor:generated-3@127.0.0.1:5432/commerce_analyst?"
        "application_name=commerce_operation_execute&"
        "options=-csearch_path%3Dtrusted_schema%2Cops%2Cpg_catalog"
    )
    assert values["PRODUCT_TRACE_DATABASE_DSN"] == (
        "postgresql://trace_writer:generated-4@127.0.0.1:5432/commerce_analyst?"
        "application_name=commerce_product_trace&"
        "options=-csearch_path%3Dapp%2Cpg_catalog"
    )
    assert values["PRODUCT_SCENARIO_RESET_DATABASE_DSN"] == (
        "postgresql://product_scenario_reset:generated-5@127.0.0.1:5432/commerce_analyst?"
        "application_name=commerce_product_scenario_reset&"
        "options=-csearch_path%3Dtrusted_schema%2Cops%2Cpg_catalog"
    )
    output = capsys.readouterr().out
    assert output == ""
    assert not any(value in output for value in values.values())


def test_complete_day4_values_are_not_rotated(tmp_path: Path) -> None:
    original = "\n".join(f"{key}=existing-{index}" for index, key in enumerate(DAY4_KEYS)) + "\n"
    env_path = _env_file(tmp_path, original)

    def unexpected_factory() -> str:
        raise AssertionError("complete credentials must not be regenerated")

    changed = ensure_day4_operations_env(
        env_path,
        token_factory=unexpected_factory,
        key_factory=unexpected_factory,
    )

    assert changed is False
    assert env_path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "content",
    [
        "PRODUCT_PROPOSAL_WRITER_PASSWORD=existing\n",
        "PRODUCT_TRACE_DATABASE_DSN=postgresql://trace_writer:x@db/name\n",
        "\n".join(f"{key}=" for key in DAY4_KEYS) + "\n",
    ],
)
def test_partial_or_empty_day4_group_fails_before_generation_or_write(
    tmp_path: Path,
    content: str,
) -> None:
    env_path = _env_file(tmp_path, content)

    def unexpected_factory() -> str:
        raise AssertionError("partial credentials must fail before generation")

    with pytest.raises(ValueError, match="partial"):
        ensure_day4_operations_env(
            env_path,
            token_factory=unexpected_factory,
            key_factory=unexpected_factory,
        )

    assert env_path.read_text(encoding="utf-8") == content


def test_provisioner_requires_an_explicitly_ignored_dot_env(tmp_path: Path) -> None:
    generated = iter(f"generated-{index}" for index in range(1, 15))
    wrong_name = tmp_path / "secrets.env"
    wrong_name.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match=r"\.env"):
        ensure_day4_operations_env(
            wrong_name,
            token_factory=_factory(generated),
            key_factory=_factory(generated),
        )

    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="ignored"):
        ensure_day4_operations_env(
            env_path,
            token_factory=_factory(generated),
            key_factory=_factory(generated),
        )


@pytest.mark.parametrize(
    "existing_key",
    [
        "PRODUCT_POSTGRES_ADMIN_PASSWORD",
        "PRODUCT_AGENT_READER_PASSWORD",
        "PRODUCT_KNOWLEDGE_READER_PASSWORD",
        "PRODUCT_CHECKPOINT_WRITER_PASSWORD",
        "PRODUCT_MODEL_STATE_WRITER_PASSWORD",
    ],
)
def test_generated_secrets_must_differ_from_known_product_role_secrets(
    tmp_path: Path,
    existing_key: str,
) -> None:
    original = f"{existing_key}=collision\n"
    env_path = _env_file(tmp_path, original)
    tokens = iter(("collision", "password-2", "password-3", "password-4", "password-5"))
    keys = iter(("key-1", "key-2"))

    with pytest.raises(ValueError, match="distinct"):
        ensure_day4_operations_env(
            env_path,
            token_factory=_factory(tokens),
            key_factory=_factory(keys),
        )

    assert env_path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("tokens", "keys"),
    [
        (("same", "same", "password-3", "password-4", "password-5"), ("key-1", "key-2")),
        (("password-1", "password-2", "password-3", "password-4", "shared"), ("shared", "key-2")),
        (("password-1", "password-2", "password-3", "password-4", "password-5"), ("same", "same")),
    ],
)
def test_generated_day4_secrets_are_pairwise_distinct(
    tmp_path: Path,
    tokens: tuple[str, ...],
    keys: tuple[str, ...],
) -> None:
    env_path = _env_file(tmp_path)

    with pytest.raises(ValueError, match="distinct"):
        ensure_day4_operations_env(
            env_path,
            token_factory=_factory(iter(tokens)),
            key_factory=_factory(iter(keys)),
        )

    assert env_path.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    ("tokens", "keys"),
    [
        (("bad/value", "password-2", "password-3", "password-4", "password-5"), ("key-1", "key-2")),
        (("password-1", "password-2", "password-3", "password-4", "password-5"), ("bad\nvalue", "key-2")),
    ],
)
def test_generated_day4_secrets_must_be_url_safe(
    tmp_path: Path,
    tokens: tuple[str, ...],
    keys: tuple[str, ...],
) -> None:
    env_path = _env_file(tmp_path)

    with pytest.raises(ValueError, match="URL-safe"):
        ensure_day4_operations_env(
            env_path,
            token_factory=_factory(iter(tokens)),
            key_factory=_factory(iter(keys)),
        )

    assert env_path.read_text(encoding="utf-8") == ""


def test_cli_reports_only_sanitized_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_path = _env_file(tmp_path)
    generated_values = tuple(f"private-{index}" for index in range(1, 8))

    main(
        env_path,
        token_factory=_factory(iter(generated_values[:5])),
        key_factory=_factory(iter(generated_values[5:])),
    )
    first_output = capsys.readouterr().out

    main(
        env_path,
        token_factory=lambda: "must-not-be-used",
        key_factory=lambda: "must-not-be-used",
    )
    second_output = capsys.readouterr().out

    assert first_output == "Day 4 operation environment created\n"
    assert second_output == "Day 4 operation environment already present\n"
    assert not any(value in first_output + second_output for value in generated_values)


def test_env_example_declares_the_complete_empty_day4_group() -> None:
    content = Path(".env.example").read_text(encoding="utf-8")
    values = dict(
        line.split("=", 1)
        for line in content.splitlines()
        if line and not line.startswith("#") and "=" in line
    )

    assert {key: values[key] for key in DAY4_KEYS} == {
        key: "" for key in DAY4_KEYS
    }
