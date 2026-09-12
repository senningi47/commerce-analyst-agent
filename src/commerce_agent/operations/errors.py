"""Stable public errors for controlled Product operations."""

import re

_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{2,127}$")


class OperationError(RuntimeError):
    """An error whose rendered form cannot disclose request details."""

    def __init__(self, reason_code: str, *, retryable: bool) -> None:
        if not _REASON_CODE.fullmatch(reason_code):
            raise ValueError("invalid operation reason code")
        super().__init__("operation request failed")
        self.reason_code = reason_code
        self.retryable = retryable


class OperationContractError(OperationError):
    """A typed request or domain invariant was invalid."""


class OperationAuthorizationError(OperationError):
    """The trusted actor lacks the required capability."""


class ApprovalError(OperationError):
    """A proposal decision or execution grant was invalid."""


class OperationConflictError(OperationError):
    """The request conflicts with immutable or versioned state."""


class OperationInfrastructureError(OperationError):
    """A known infrastructure failure occurred before commit."""


class OperationOutcomeUnknown(OperationError):
    """The commit result must be recovered through an execution lookup."""
