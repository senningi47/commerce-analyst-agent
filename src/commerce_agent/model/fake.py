"""Deterministic model adapter used by unit tests."""

from collections import deque
from collections.abc import Iterable

from commerce_agent.model.contracts import ModelRequest, ModelResponse
from commerce_agent.model.errors import ModelGatewayError, ModelStateError


class FakeModelExhausted(ModelStateError):
    """Raised when a test did not configure a response for a model call."""


class FakeModel:
    """Return preconfigured model responses in first-in, first-out order."""

    def __init__(self, script: Iterable[ModelResponse | ModelGatewayError]) -> None:
        self._script = deque(script)
        self._requests: list[ModelRequest] = []

    @property
    def requests(self) -> tuple[ModelRequest, ...]:
        """Return an immutable snapshot of every received request."""

        return tuple(self._requests)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Return the next configured response or fail explicitly."""

        self._requests.append(request)
        if not self._script:
            raise FakeModelExhausted(
                "fake_model_exhausted",
                "fake model has no configured response",
                retryable=False,
            )
        scripted = self._script.popleft()
        if isinstance(scripted, ModelGatewayError):
            raise scripted
        return scripted
