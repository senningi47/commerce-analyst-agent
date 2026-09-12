import pytest

from commerce_agent.operations.errors import (
    ApprovalError,
    OperationAuthorizationError,
    OperationConflictError,
    OperationContractError,
    OperationInfrastructureError,
    OperationOutcomeUnknown,
)


@pytest.mark.parametrize(
    "error_type",
    [
        OperationContractError,
        OperationAuthorizationError,
        ApprovalError,
        OperationConflictError,
        OperationInfrastructureError,
        OperationOutcomeUnknown,
    ],
)
def test_operation_errors_are_stable_and_sanitized(error_type: type[Exception]) -> None:
    error = error_type("signature_invalid", retryable=False)
    rendered = f"{error!s}|{error!r}"

    assert error.reason_code == "signature_invalid"  # type: ignore[attr-defined]
    assert error.retryable is False  # type: ignore[attr-defined]
    assert "signature_invalid" not in rendered
    assert "nonce" not in rendered.casefold()
    assert "signature" not in rendered.casefold()


def test_operation_error_rejects_secret_bearing_constructor_arguments() -> None:
    with pytest.raises(TypeError):
        ApprovalError("signature_invalid", retryable=False, message="nonce=private")  # type: ignore[call-arg]
