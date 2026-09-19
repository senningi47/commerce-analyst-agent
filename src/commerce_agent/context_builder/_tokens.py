"""Provider-wire token estimation contract and data-only tokenizer adapter."""

import json
import re
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from commerce_agent.context_builder._canonical import canonical_json
from commerce_agent.model.contracts import ModelRequest


class TokenEstimate(BaseModel, frozen=True, extra="forbid"):
    input_tokens: int = Field(ge=0)
    estimator_revision: str = Field(min_length=1, max_length=128)


class TokenEstimator(Protocol):
    @property
    def revision(self) -> str:
        """Return the immutable estimator snapshot revision."""

    def estimate(self, request: ModelRequest) -> TokenEstimate:
        """Estimate only the provider wire-visible request payload."""


class TokenizerDataFile(BaseModel, frozen=True, extra="forbid"):
    name: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=1, le=16 * 1024 * 1024)

    @model_validator(mode="after")
    def validate_name(self) -> "TokenizerDataFile":
        path = PurePosixPath(self.name)
        if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".json":
            raise ValueError("tokenizer data file must be a safe JSON path")
        return self


class TokenizerExcludedFile(BaseModel, frozen=True, extra="forbid"):
    """A hash-locked archive file that is intentionally never installed."""

    name: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0, le=16 * 1024 * 1024)

    @model_validator(mode="after")
    def validate_name(self) -> "TokenizerExcludedFile":
        path = PurePosixPath(self.name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("tokenizer excluded file must be a safe path")
        return self


class TokenizerArtifactManifest(BaseModel, frozen=True, extra="forbid"):
    estimator_revision: str = Field(min_length=1, max_length=128)
    renderer_revision: Literal["deepseek-chat-renderer-v1"]
    files: tuple[TokenizerDataFile, ...] = Field(min_length=1, max_length=64)
    directories: tuple[str, ...] = Field(default=(), max_length=64)
    excluded_entries: tuple[TokenizerExcludedFile, ...] = Field(default=(), max_length=1)


class ProvisionedDeepSeekTokenEstimator:
    """Estimate a normalized request using only reviewed JSON tokenizer data."""

    def __init__(self, artifact_root: Path, manifest: TokenizerArtifactManifest) -> None:
        self._manifest = manifest
        data: dict[str, object] | None = None
        for entry in manifest.files:
            path = artifact_root.joinpath(*PurePosixPath(entry.name).parts)
            content = path.read_bytes()
            if len(content) != entry.size or sha256(content).hexdigest() != entry.sha256:
                raise ValueError("tokenizer data file does not match reviewed manifest")
            if PurePosixPath(entry.name).name == "tokenizer.json":
                loaded = json.loads(content)
                if not isinstance(loaded, dict):
                    raise ValueError("tokenizer JSON must be an object")
                data = loaded
        if data is None:
            raise ValueError("reviewed manifest must include tokenizer.json")
        model = data.get("model")
        if not isinstance(model, dict) or model.get("type") not in {"WordLevel", "BPE"}:
            raise ValueError("unsupported data-only tokenizer model")
        vocab = model.get("vocab")
        if not isinstance(vocab, dict) or not vocab:
            raise ValueError("tokenizer vocabulary must be a non-empty object")
        self._vocab = frozenset(str(token) for token in vocab)

    @property
    def revision(self) -> str:
        return self._manifest.estimator_revision

    def estimate(self, request: ModelRequest) -> TokenEstimate:
        wire = {
            "messages": [message.model_dump(mode="json") for message in request.messages],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": json.loads(tool.parameters_json),
                    },
                }
                for tool in request.tools
            ],
        }
        rendered = canonical_json(wire)
        visible_tokens = re.findall(r"\w+|[^\w\s]", rendered, flags=re.UNICODE)
        public_count = sum(
            1 if token in self._vocab else max(1, (len(token.encode("utf-8")) + 3) // 4)
            for token in visible_tokens
        )
        private_count = sum(group.provider_turn_ref.token_weight for group in request.history)
        return TokenEstimate(
            input_tokens=public_count + private_count,
            estimator_revision=self.revision,
        )
