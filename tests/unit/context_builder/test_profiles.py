import json
import shutil
from pathlib import Path

import pytest

from commerce_agent.context_builder.contracts import DataNamespace, RunProfileKey
from commerce_agent.context_builder.profiles import ProfileRegistry, RevisionMismatch

CONFIG_ROOT = Path(__file__).parents[3] / "configs" / "model"
FIXTURE_ROOT = Path(__file__).parents[2] / "fixtures" / "orchestration"


@pytest.fixture
def registry() -> ProfileRegistry:
    return ProfileRegistry.load(CONFIG_ROOT)


@pytest.mark.parametrize(
    ("profile", "allowed", "denied"),
    [
        ("retail", "retail_knowledge", "bird_official_feedback"),
        ("bird_a", "bird_a_tool_result", "retail_knowledge"),
        ("bird_c", "bird_c_phase", "product_query_result"),
    ],
)
def test_profiles_have_disjoint_namespaces(
    registry: ProfileRegistry,
    profile: RunProfileKey,
    allowed: DataNamespace,
    denied: DataNamespace,
) -> None:
    loaded = registry.get(profile)
    assert allowed in loaded.allowed_namespaces
    assert denied not in loaded.allowed_namespaces


def test_profiles_expose_only_the_exact_registered_tools(
    registry: ProfileRegistry,
) -> None:
    assert registry.tool_names("retail") == {
        "retrieve_retail_knowledge",
        "resolve_business_value",
        "request_clarification",
        "submit_investigation_plan",
        "submit_sql_candidate",
        "propose_operation",
        "submit_investigation_report",
    }
    assert registry.tool_names("bird_a") == {
        "execute_sql",
        "get_schema",
        "get_all_column_meanings",
        "get_column_meaning",
        "get_all_external_knowledge_names",
        "get_knowledge_definition",
        "get_all_knowledge_definitions",
        "ask_user",
        "submit_sql",
    }
    assert registry.tool_names("bird_c") == {"ask_user", "submit_sql"}
    assert all(
        '"additionalProperties":false' in tool.parameters_json
        for key in RunProfileKey
        for tool in registry.tools_for(key)
    )


def test_sql_reasoning_steps_exist_only_in_retail_profile(
    registry: ProfileRegistry,
) -> None:
    sql_steps = {"retail_sql_generate", "retail_sql_repair"}
    retail_steps = {rule.step.value for rule in registry.get("retail").inference_rules}
    assert sql_steps <= retail_steps
    assert {tool.name for tool in registry.tools_for_step("retail", "retail_sql_generate")} == {
        "submit_sql_candidate"
    }
    for key in ("bird_a", "bird_c"):
        assert not (sql_steps & {rule.step.value for rule in registry.get(key).inference_rules})


def test_day4_retail_profile_has_only_reviewed_step_tools(
    registry: ProfileRegistry,
) -> None:
    expected = {
        "retail_decide": {
            "retrieve_retail_knowledge",
            "resolve_business_value",
            "request_clarification",
            "submit_investigation_plan",
        },
        "retail_clarify": {"request_clarification"},
        "retail_sql_generate": {"submit_sql_candidate"},
        "retail_sql_repair": {"submit_sql_candidate"},
        "retail_report": {"propose_operation", "submit_investigation_report"},
    }

    assert registry.step_tools("retail") == expected
    assert "execute_readonly_sql" not in registry.tool_names("retail")
    assert "approve_operation" not in registry.tool_names("retail")
    assert "execute_operation" not in registry.tool_names("retail")


@pytest.mark.parametrize(
    "name",
    [
        "request_clarification",
        "submit_investigation_plan",
        "submit_sql_candidate",
        "propose_operation",
        "submit_investigation_report",
    ],
)
def test_day4_tool_schema_is_closed(name: str, registry: ProfileRegistry) -> None:
    assert registry.schema_for("retail", name)["additionalProperties"] is False


def test_profile_loader_rejects_mismatched_revisions(tmp_path: Path) -> None:
    root = tmp_path / "model"
    shutil.copytree(CONFIG_ROOT, root)
    path = root / "run-profiles.v2.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["profiles"][0]["tool_catalog_revision"] = "wrong"
    path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(RevisionMismatch) as caught:
        ProfileRegistry.load(root)
    assert caught.value.reason_code == "revision_mismatch"


def test_synthetic_runtime_manifest_matches_fixture_and_is_isolated() -> None:
    runtime = (CONFIG_ROOT / "bird-tools.synthetic.v1.json").read_bytes()
    fixture = (FIXTURE_ROOT / "bird-tools.synthetic.v1.json").read_bytes()
    assert runtime == fixture
    lowered = runtime.decode("utf-8").lower()
    assert "synthetic" in lowered
    for forbidden in (
        "olist",
        "retail",
        "resolver",
        "5432",
        "6002",
        "dsn",
    ):
        assert forbidden not in lowered


def test_loader_rejects_noncanonical_or_unknown_config(tmp_path: Path) -> None:
    root = tmp_path / "model"
    shutil.copytree(CONFIG_ROOT, root)
    path = root / "prompt-policies.v2.json"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(RevisionMismatch):
        ProfileRegistry.load(root)
