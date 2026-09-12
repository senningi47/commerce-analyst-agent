"""Contract tests for BIRD GT isolation (structural, name-level assertions).

Three groups (v0.3 §5.1, §14.4):
1. the system-agent build-context file list carries no GT/data tokens;
2. the root .dockerignore is an allowlist that never re-includes restricted
   paths, and the Dockerfile copies only allowlisted paths;
3. compose.bird.yaml gives the system agent exactly one volume — its own
   agent-visible JSONL spool (v0.3 §18) — and no data/database/simulator-secret
   env names, while the db environment keeps its read-only public-data mount;
   every env value is a ``${VAR}`` reference, never an inline secret.
"""

import re
from pathlib import Path

import pytest

from commerce_agent.evaluation.contracts import load_official_contract

REPO_ROOT = Path(__file__).parents[2]
AGENT_DIR = REPO_ROOT / "bird_system_agent"
FORBIDDEN_NAME_TOKENS = ("data", "evaluator_only", "sol_sql", "test_cases", "gt")

# env names the system agent must never need: data, database, and simulator
# credentials travel only to the official orchestrator/db-env/user-sim sides
AGENT_FORBIDDEN_ENV_NAMES = (
    "PG_USER",
    "PG_PASSWORD",
    "BIRD_PUBLIC_DATA_DIR",
    "LITELLM_API_KEY",
    "LITELLM_API_BASE",
    "USER_SIM_MODEL",
)


def _agent_files() -> list[Path]:
    return sorted(path for path in AGENT_DIR.rglob("*") if path.is_file())


def test_system_agent_file_names_carry_no_gt_tokens() -> None:
    files = _agent_files()
    assert files, "system agent build context must not be empty"
    for path in files:
        lowered = path.relative_to(REPO_ROOT).as_posix().lower()
        for token in FORBIDDEN_NAME_TOKENS:
            assert token not in lowered, f"{lowered} contains forbidden token {token!r}"


def test_dockerignore_is_allowlist_without_restricted_unexcludes() -> None:
    text = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]

    assert "**" in lines, "allowlist style requires a global exclude"
    negations = {line[1:] for line in lines if line.startswith("!")}
    for restricted in ("data", "_upstream", "tests", "data/", "_upstream/", "tests/"):
        assert restricted not in negations, f".dockerignore re-includes {restricted}"
    for required in ("src", "configs", "bird_system_agent", "pyproject.toml"):
        assert required in negations, f".dockerignore must allow {required}"


def test_dockerfile_copies_only_allowlisted_paths() -> None:
    text = (REPO_ROOT / "bird_system_agent" / "Dockerfile").read_text(encoding="utf-8")
    copies = re.findall(r"^COPY\s+(\S+)", text, flags=re.MULTILINE)
    assert set(copies) == {
        "bird_system_agent/requirements.txt",
        "pyproject.toml",
        "src/commerce_agent",
        "configs/model",
        "bird_system_agent",
        ".cache/commerce-agent/deepseek-tokenizer",
    }
    assert not any("data" in copy.lower() or "upstream" in copy.lower() for copy in copies)
    assert re.search(r"^USER\s+\S+", text, flags=re.MULTILINE), "image must drop root"
    assert "MUST-PIN" in text, "base image digest pinning must stay visible until done"


def _service_block(compose_text: str, service: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  \S|\Z)",
        compose_text,
    )
    assert match is not None, f"service {service} missing from compose.bird.yaml"
    return match.group(1)


def _volume_entries(block: str) -> list[str]:
    """Return the list entries of the block's `volumes:` subsection only."""

    entries: list[str] = []
    in_volumes = False
    for line in block.splitlines():
        if line.startswith("    ") and line[4:5] not in (" ", ""):
            in_volumes = line.strip() == "volumes:"
            continue
        if in_volumes and line.strip().startswith("- "):
            entries.append(line.strip()[2:].strip())
    return entries


def test_system_agent_mounts_only_agent_visible_spool() -> None:
    compose_text = (REPO_ROOT / "compose.bird.yaml").read_text(encoding="utf-8")
    block = _service_block(compose_text, "bird-system-agent")

    assert _volume_entries(block) == ["./outputs/bird-agent-spool:/app/spool"], (
        "system agent mounts exactly one rw volume: its own agent-visible spool (v0.3 §18)"
    )
    assert re.search(r"^\s+BIRD_SPOOL_DIR:", block, flags=re.MULTILINE), (
        "system agent spool path env must be declared"
    )
    for name in AGENT_FORBIDDEN_ENV_NAMES:
        assert not re.search(rf"^\s+{name}:", block, flags=re.MULTILINE), (
            f"system agent must not receive {name}"
        )


def test_agent_spool_host_dir_is_gitignored() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "outputs/bird-agent-spool/" in gitignore


def test_compose_env_values_are_references_or_static_hosts() -> None:
    compose_text = (REPO_ROOT / "compose.bird.yaml").read_text(encoding="utf-8")
    value_pattern = re.compile(r"^\s+[A-Z_][A-Z0-9_]*:\s+(.+)$", flags=re.MULTILINE)
    for match in value_pattern.finditer(compose_text):
        value = match.group(1).strip()
        assert value.startswith(("${", '"${')) or re.fullmatch(
            r"""['"]?[\w.\-/@:]+['"]?""", value
        ), f"unexpected inline value: {value!r}"
        assert "api.sk" not in value.lower()
        assert not value.startswith("postgres")


def test_db_environment_keeps_read_only_public_data_mount() -> None:
    compose_text = (REPO_ROOT / "compose.bird.yaml").read_text(encoding="utf-8")
    block = _service_block(compose_text, "bird-db-environment")

    volume_match = re.search(
        r"^\s+-\s+\$\{BIRD_PUBLIC_DATA_DIR[^}]*\}:(\S+):ro$", block, flags=re.MULTILINE
    )
    assert volume_match is not None, "public data must be a read-only mount"
    assert volume_match.group(1) == "/opt/bird-adk/bird-interact-full"


def test_official_env_names_come_from_frozen_contract_only() -> None:
    contract = load_official_contract()
    for name in ("SYSTEM_AGENT_PORT", "DB_ENV_PORT", "USER_SIM_PORT", "PATIENCE"):
        assert name in contract.orchestrator_env_names


@pytest.mark.parametrize("dockerfile", ["bird-db-environment.Dockerfile", "bird-user-simulator.Dockerfile"])
def test_spike_dockerfiles_never_reference_data_paths(dockerfile: str) -> None:
    text = (REPO_ROOT / "scripts" / "spikes" / dockerfile).read_text(encoding="utf-8")
    # path-shaped tokens only: comments may legitimately mention words like
    # "database"; the assertion targets path/provisioning references
    for token in ("data/", "evaluator_only", "sol_sql", "test_cases", "gt_", "/data"):
        assert token not in text.lower(), f"{dockerfile} references {token!r}"
