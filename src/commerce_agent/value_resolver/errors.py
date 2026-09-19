"""Stable, non-secret error taxonomy for business-value resolution."""


class ValueResolverError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class ValueDomainUnavailable(ValueResolverError):
    pass


class ValueResolutionInfrastructureError(ValueResolverError):
    pass


class ValueResolutionDataError(ValueResolverError):
    pass
