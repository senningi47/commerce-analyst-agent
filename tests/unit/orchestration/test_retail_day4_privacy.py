import inspect

import pytest

from commerce_agent.orchestration._checkpoint import (
    CheckpointIncompatible,
    canonical_checkpoint_json,
)
from commerce_agent.orchestration.retail_graph import RetailGraph
from commerce_agent.orchestration.tools import ProposalWorkflowPort


def test_retail_graph_constructor_has_no_approval_or_reset_capability() -> None:
    parameters = inspect.signature(RetailGraph).parameters

    assert "operation_workflow" in parameters
    assert "approval_signer" not in parameters
    assert "approval_store" not in parameters
    assert "reset_port" not in parameters
    public_methods = {
        name
        for name, value in ProposalWorkflowPort.__dict__.items()
        if not name.startswith("_") and callable(value)
    }
    assert public_methods == {"propose"}


@pytest.mark.parametrize(
    "key", ["grant", "nonce", "signature", "hmac", "dsn", "seller_id", "reasoning_content"]
)
def test_checkpoint_state_rejects_sensitive_keys(key: str) -> None:
    with pytest.raises(CheckpointIncompatible) as caught:
        canonical_checkpoint_json(
            {
                "state_schema_revision": "retail-state-v2",
                "node_revision": "retail-nodes-v2",
                key: "private",
            }
        )
    assert caught.value.reason_code == "sensitive_checkpoint_field"
