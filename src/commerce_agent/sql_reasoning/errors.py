"""Sanitized SQL reasoning failures."""


class SqlReasoningError(RuntimeError):
    def __init__(self, reason_code: str, *, retryable: bool = False) -> None:
        super().__init__("SQL reasoning failed")
        self.reason_code = reason_code
        self.retryable = retryable


class SqlReasoningContractError(SqlReasoningError):
    pass


class SqlNoProgress(SqlReasoningError):
    pass
