"""Stable, non-secret error taxonomy for versioned Knowledge."""


class KnowledgeError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class KnowledgeRevisionUnavailable(KnowledgeError):
    pass


class KnowledgeCatalogInvalid(KnowledgeError):
    pass


class KnowledgeInfrastructureError(KnowledgeError):
    pass
