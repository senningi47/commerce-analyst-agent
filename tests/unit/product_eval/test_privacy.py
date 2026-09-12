import ast
import inspect
import json
from pathlib import Path

import pytest

from commerce_agent.operations.workflow import OperationWorkflow
from commerce_agent.orchestration.retail_graph import RetailGraph
from commerce_agent.product_eval.driver import ProductScenarioDriver
from tests.unit.product_eval.test_day4_scenarios import (
    in_memory_driver,
    load_day4_scenario,
)

ROOT = Path(__file__).parents[3]
SOURCE_ROOT = ROOT / "src" / "commerce_agent"
APPROVED_FIXTURES = (
    ROOT / "tests" / "fixtures" / "product_eval" / "day4-development.v1.json",
    ROOT / "tests" / "fixtures" / "product_eval" / "day4-regression.v1.json",
)


def _imports(source: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_production_modules_do_not_import_product_eval() -> None:
    product_eval_root = SOURCE_ROOT / "product_eval"
    for source in SOURCE_ROOT.rglob("*.py"):
        if product_eval_root in source.parents:
            continue
        assert not any(
            module == "commerce_agent.product_eval"
            or module.startswith("commerce_agent.product_eval.")
            for module in _imports(source)
        )


def test_driver_constructor_requires_reset_port_but_runtime_components_do_not() -> None:
    assert "reset" in inspect.signature(ProductScenarioDriver).parameters
    assert "reset" not in inspect.signature(RetailGraph).parameters
    assert "reset" not in inspect.signature(OperationWorkflow).parameters


def test_only_approved_fixture_files_have_public_non_secret_content() -> None:
    forbidden = {
        "authorization: basic",
        "authorization: bearer",
        "connection_string",
        "dsn=",
        "evaluator-only",
        "evaluator_only",
        "final_closed",
        "hidden_answer",
        "password",
        "postgresql://",
        "provider_payload",
        "reasoning_content",
        "reset_secret",
        "secret_key",
    }
    for path in APPROVED_FIXTURES:
        text = path.read_text(encoding="utf-8")
        document = json.loads(text)

        assert document["fixture_revision"] in {
            "day4-development-v1",
            "day4-regression-v1",
        }
        assert not any(token in text.casefold() for token in forbidden)


@pytest.mark.asyncio
async def test_generated_public_results_have_no_private_runtime_fields() -> None:
    forbidden_keys = {
        "authentication_ref",
        "dsn",
        "grant",
        "nonce",
        "reasoning_content",
        "reset",
        "seller_id",
        "signature",
    }
    for scenario_id in (
        "seller-risk-investigation-v1",
        "metric-alert-to-investigation-v1",
        "ambiguous-gmv-investigation-v1",
    ):
        scenario = load_day4_scenario(scenario_id)
        driver, _store = await in_memory_driver(scenario)
        result = await driver.run(scenario)
        public_json = result.model_dump_json().casefold()

        assert not any(f'"{key}"' in public_json for key in forbidden_keys)
