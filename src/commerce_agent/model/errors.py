"""Stable, sanitized error taxonomy for model gateways."""

import re

from commerce_agent.model.contracts import ModelAttemptSummary

_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{2,127}$")
_SENSITIVE_MESSAGE = re.compile(
    r"(?:"
    r"\b(?:postgres(?:ql)?|mysql|redis)://|"
    r"\bsk-[A-Za-z0-9_-]+|"
    r"\b(?:api[_ -]?key|authorization|bearer|dsn|password|secret)\b|"
    r"(?:reasoning|provider_payload|assistant_message)|"
    r"[{}\[\]]|"
    r"\r|\n"
    r")",
    flags=re.IGNORECASE,
)
_GENERIC_MESSAGE = "model gateway operation failed"


def _sanitize_message(message: str) -> str:
    normalized = message.strip()
    if not normalized or len(normalized) > 256 or _SENSITIVE_MESSAGE.search(normalized):
        return _GENERIC_MESSAGE
    return normalized


class ModelGatewayError(RuntimeError):
    """Base class carrying only stable public metadata and sanitized attempts."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        retryable: bool,
        charge_ambiguous: bool = False,
        attempts: tuple[ModelAttemptSummary, ...] = (),
    ) -> None:
        if not _REASON_CODE.fullmatch(reason_code):
            raise ValueError("reason_code must use stable lowercase underscore syntax")
        super().__init__(_sanitize_message(message))
        self.reason_code = reason_code
        self.retryable = retryable
        self.charge_ambiguous = charge_ambiguous
        self.attempts = attempts


class ModelConfigurationError(ModelGatewayError):
    """The local model configuration is absent or internally inconsistent."""


class ModelCapabilityError(ModelGatewayError):
    """The request is unsupported by the reviewed provider capability snapshot."""


class ModelStateError(ModelGatewayError):
    """A provider-private turn reference is unavailable or invalid."""


class ModelAuthError(ModelGatewayError):
    """The provider rejected authentication."""


class ModelBalanceError(ModelGatewayError):
    """The provider reported insufficient account balance."""


class ModelRateLimited(ModelGatewayError):
    """The bounded rate-limit retry policy was exhausted."""


class ModelTransportError(ModelGatewayError):
    """A provider transport attempt failed."""


class ModelProtocolError(ModelGatewayError):
    """A successful transport returned an invalid provider payload."""
