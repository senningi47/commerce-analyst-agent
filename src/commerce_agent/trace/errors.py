"""Sanitized Trace failures."""


class TraceContractError(RuntimeError):
    def __init__(self, reason_code: str, *, retryable: bool = False) -> None:
        super().__init__("trace operation failed")
        self.reason_code = reason_code
        self.retryable = retryable
