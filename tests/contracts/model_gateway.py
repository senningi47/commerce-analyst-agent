"""Reusable public response assertions for every ModelGateway adapter."""

from commerce_agent.model.contracts import (
    FinalOutput,
    FinishReason,
    ModelResponse,
    ToolCallOutput,
)


def assert_model_response_contract(response: ModelResponse) -> None:
    """Assert the provider-neutral invariants shared by fake and real adapters."""

    assert tuple(attempt.attempt_number for attempt in response.attempts) == tuple(
        range(1, len(response.attempts) + 1)
    )
    assert response.attempts[-1].outcome == "success"
    assert response.usage == response.attempts[-1].usage
    assert response.cost == response.attempts[-1].cost
    if isinstance(response.output, ToolCallOutput):
        assert response.finish_reason is FinishReason.TOOL_CALLS
        assert tuple(call.call_id for call in response.output.tool_calls) == (
            response.provider_turn_ref.expected_tool_call_ids
        )
    else:
        assert isinstance(response.output, FinalOutput)
        assert response.finish_reason is not FinishReason.TOOL_CALLS
        assert response.provider_turn_ref.expected_tool_call_ids == ()

    serialized = response.model_dump_json()
    for forbidden in ("reasoning_content", "provider_payload", "assistant_message"):
        assert forbidden not in serialized
