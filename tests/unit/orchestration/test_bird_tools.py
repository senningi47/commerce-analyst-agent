import json
from pathlib import Path

import pytest

from commerce_agent.model.contracts import ToolCall
from commerce_agent.orchestration.tools import SyntheticBirdAToolPort, ToolContractError

SYNTHETIC_MANIFEST = json.loads(
    (
        Path(__file__).parents[2]
        / "fixtures"
        / "orchestration"
        / "bird-a-runtime.synthetic.v1.json"
    ).read_text(encoding="utf-8")
)


def tool_call(name: str, arguments: dict[str, str]) -> ToolCall:
    return ToolCall(
        call_id="bird_call_1",
        name=name,
        arguments_json=json.dumps(arguments, sort_keys=True, separators=(",", ":")),
    )


@pytest.mark.asyncio
async def test_bird_a_port_executes_only_frozen_synthetic_manifest() -> None:
    port = SyntheticBirdAToolPort.from_fixture(SYNTHETIC_MANIFEST)

    observed = await port.execute(
        tool_call(
            "synthetic_bird_a_observe_schema",
            {"schema_ref": "synthetic:orders"},
        )
    )

    assert observed.status == "success"
    assert observed.source_refs == ("synthetic:orders",)
    with pytest.raises(ToolContractError) as caught:
        await port.execute(tool_call("execute_readonly_sql", {"sql": "SELECT 1"}))
    assert caught.value.reason_code == "tool_not_registered"


@pytest.mark.asyncio
async def test_bird_a_port_executes_only_sql_present_in_fixture() -> None:
    port = SyntheticBirdAToolPort.from_fixture(SYNTHETIC_MANIFEST)
    sql = "SELECT COUNT(*) AS order_count FROM synthetic_orders"

    result = await port.execute(tool_call("synthetic_bird_a_execute_readonly_sql", {"sql": sql}))

    assert json.loads(result.content_json) == {
        "columns": ["order_count"],
        "rows": [{"order_count": 8}],
    }
    assert result.source_refs == ("synthetic-query:" + result.content_sha256,)
    with pytest.raises(ToolContractError) as caught:
        await port.execute(tool_call("synthetic_bird_a_execute_readonly_sql", {"sql": "SELECT 1"}))
    assert caught.value.reason_code == "synthetic_fixture_not_found"


@pytest.mark.parametrize(
    "fixture",
    [
        {
            "revision": "bird-a-runtime-fixture-v1",
            "schemas": {
                "retail:orders": {
                    "columns": ("status",),
                    "table": "retail_orders",
                }
            },
            "queries": {},
        },
        {
            "revision": "bird-a-runtime-fixture-v1",
            "schemas": {
                "synthetic:orders": {
                    "columns": ("x" * 70_000,),
                    "table": "synthetic_orders",
                }
            },
            "queries": {},
        },
        {
            "revision": "bird-a-runtime-fixture-v1",
            "schemas": {},
            "queries": {
                "SELECT COUNT(*) AS order_count FROM synthetic_orders": {
                    "columns": ("order_count",),
                    "rows": ({"different_column": 8},),
                }
            },
        },
    ],
)
def test_bird_a_port_rejects_unsafe_or_inconsistent_fixture(
    fixture: dict[str, object],
) -> None:
    with pytest.raises(ToolContractError) as caught:
        SyntheticBirdAToolPort.from_fixture(fixture)

    assert caught.value.reason_code == "invalid_synthetic_manifest"
