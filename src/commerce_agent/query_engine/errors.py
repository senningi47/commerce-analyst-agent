class QueryEngineError(RuntimeError):
    """Base error with a stable, non-secret reason code."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class SqlPolicyViolation(QueryEngineError):
    pass


class SqlPlanRejected(QueryEngineError):
    pass


class SqlExecutionError(QueryEngineError):
    def __init__(
        self,
        reason_code: str,
        message: str,
        sqlstate: str | None = None,
    ) -> None:
        super().__init__(reason_code, message)
        self.sqlstate = sqlstate


class SqlResultLimitExceeded(QueryEngineError):
    pass


class QueryInfrastructureError(QueryEngineError):
    pass


class ReconciliationContractError(QueryEngineError):
    pass
