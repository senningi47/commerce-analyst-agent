"""Deterministic context assembly, isolation, trimming, and audit hashes."""

from collections.abc import Iterable
from decimal import Decimal

from pydantic import ValidationError

from commerce_agent.context_builder._canonical import canonical_json, sha256_canonical
from commerce_agent.context_builder._tokens import TokenEstimator
from commerce_agent.context_builder.contracts import (
    ContextDatum,
    ContextRequest,
    PromptBundle,
    RunProfile,
    TrimmingSummary,
    TypedErrorDatum,
)
from commerce_agent.context_builder.profiles import ProfileRegistry, RevisionMismatch
from commerce_agent.model.contracts import (
    ChatMessage,
    ConversationGroup,
    InferenceConfig,
    ModelRequest,
    RunScope,
    ToolDefinition,
    ToolExchangeGroup,
    ToolResult,
)

_ZERO_HASH = "0" * 64


class ContextBuilderError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.retryable = False


class ProfileMismatch(ContextBuilderError):
    pass


class DataNamespaceViolation(ContextBuilderError):
    pass


class ContextIntegrityError(ContextBuilderError):
    pass


class ToolResultMismatch(ContextBuilderError):
    pass


class ContextBudgetExceeded(ContextBuilderError):
    pass


def compute_config_hash(
    profile: RunProfile,
    inference: InferenceConfig,
    requested_model: str,
) -> str:
    del inference
    return sha256_canonical(
        {
            "model": requested_model,
            "inference_rules": profile.inference_rules,
            "budgets": {
                "input_token_limit": profile.input_token_limit,
                "minimum_recent_groups": profile.minimum_recent_groups,
                "tool_result_max_bytes": profile.tool_result_max_bytes,
            },
            "capability_revision": profile.capability_revision,
            "estimator_revision": profile.estimator_revision,
            "retry_revision": "deepseek-retry-v1",
            "canonicalization_revision": profile.canonicalization_revision,
        }
    )


def compute_prompt_policy_hash(common_policy: str, profile_policy: str) -> str:
    return sha256_canonical({"common": common_policy, "profile": profile_policy})


def compute_rendered_prompt_hash(messages: tuple[ChatMessage, ...]) -> str:
    return sha256_canonical({"messages": messages})


def compute_tool_hash(profile: RunProfile, tools: tuple[ToolDefinition, ...]) -> str:
    return sha256_canonical(
        {
            "profile": profile.key,
            "catalog_revision": profile.tool_catalog_revision,
            "tools": tools,
        }
    )


def scope_digest(scope: RunScope) -> str:
    return sha256_canonical(scope.model_dump(mode="python"))


def _datum_row(datum: ContextDatum) -> dict[str, object]:
    return {
        "record_type": datum.kind,
        "namespace": datum.namespace,
        "source_ref": datum.source_ref,
        "revision": datum.revision,
        "digest": datum.digest,
        "content": datum.content,
    }


def _group_digest(group: ConversationGroup) -> str:
    return sha256_canonical(group)


def compute_context_hash(
    records: tuple[ContextDatum, ...],
    history: tuple[ConversationGroup, ...],
    trimming: TrimmingSummary,
    latest_error: TypedErrorDatum | None = None,
) -> str:
    record_rows: list[dict[str, object]] = [
        {
            "source_ref": datum.source_ref,
            "revision": datum.revision,
            "digest": datum.digest,
        }
        for datum in records
    ]
    if latest_error is not None:
        record_rows.append(
            {
                "source_ref": latest_error.error_type,
                "revision": None,
                "digest": latest_error.digest,
            }
        )
    return sha256_canonical(
        {
            "records": record_rows,
            "groups": [_group_digest(group) for group in history],
            "trimming": trimming,
        }
    )


def _summarize_result(result: ToolResult) -> ToolResult:
    summary_json = canonical_json(
        {
            "content_sha256": result.content_sha256,
            "deterministic_summary": result.deterministic_summary,
            "error_class": result.error_class,
        }
    )
    return result.model_copy(update={"content_json": summary_json, "content_mode": "summary"})


class ContextBuilder:
    """Build one provider-neutral request from one exact registered profile."""

    def __init__(
        self,
        *,
        registry: ProfileRegistry,
        estimator: TokenEstimator,
        requested_model: str,
        timeout_seconds: Decimal,
        provider_user_id: str,
    ) -> None:
        self._registry = registry
        self._estimator = estimator
        self._requested_model = requested_model
        self._timeout_seconds = timeout_seconds
        self._provider_user_id = provider_user_id

    def build(self, request: ContextRequest) -> PromptBundle:
        profile = self._validate_request(request)
        inference = next(
            rule.inference for rule in profile.inference_rules if rule.step == request.step
        )
        config_hash = compute_config_hash(profile, inference, self._requested_model)
        if request.run_scope.config_hash != config_hash:
            raise RevisionMismatch()

        history = list(request.history)
        evidence = list(request.evidence)
        removed_groups: list[str] = []
        compressed_results: list[str] = []
        removed_evidence: list[str] = []
        original_groups = len(history)

        candidate = self._model_request(
            request, profile, inference, tuple(history), tuple(evidence), config_hash
        )
        before = self._estimate(candidate, profile)
        after = before

        while after > profile.input_token_limit and len(history) > profile.minimum_recent_groups:
            removed_groups.append(_group_digest(history.pop(0)))
            candidate = self._model_request(
                request, profile, inference, tuple(history), tuple(evidence), config_hash
            )
            after = self._estimate(candidate, profile)

        for group_index, group in enumerate(tuple(history)):
            if after <= profile.input_token_limit:
                break
            if not isinstance(group, ToolExchangeGroup):
                continue
            results = list(group.tool_results)
            for result_index, result in enumerate(tuple(results)):
                if after <= profile.input_token_limit:
                    break
                if result.content_mode != "raw":
                    continue
                compressed_results.append(result.content_sha256)
                results[result_index] = _summarize_result(result)
                history[group_index] = group.model_copy(update={"tool_results": tuple(results)})
                candidate = self._model_request(
                    request, profile, inference, tuple(history), tuple(evidence), config_hash
                )
                after = self._estimate(candidate, profile)

        if after > profile.input_token_limit:
            last_by_identity: dict[tuple[object, ...], int] = {}
            for index, datum in enumerate(evidence):
                last_by_identity[(datum.kind, datum.namespace, datum.source_ref)] = index
            retained_evidence: list[ContextDatum] = []
            for index, datum in enumerate(evidence):
                identity = (datum.kind, datum.namespace, datum.source_ref)
                if last_by_identity[identity] != index:
                    removed_evidence.append(datum.digest)
                else:
                    retained_evidence.append(datum)
            evidence = retained_evidence
            candidate = self._model_request(
                request, profile, inference, tuple(history), tuple(evidence), config_hash
            )
            after = self._estimate(candidate, profile)

        if after > profile.input_token_limit:
            raise ContextBudgetExceeded(
                "mandatory_context_over_budget",
                "mandatory context exceeds the registered input budget",
            )

        trimming = TrimmingSummary(
            original_groups=original_groups,
            retained_groups=len(history),
            removed_group_digests=tuple(removed_groups),
            compressed_result_digests=tuple(compressed_results),
            removed_evidence_digests=tuple(removed_evidence),
            estimated_before=before,
            estimated_after=after,
        )
        final_request, hashes = self._final_request(
            request,
            profile,
            inference,
            tuple(history),
            tuple(evidence),
            config_hash,
            trimming,
        )
        if self._estimate(final_request, profile) != after:
            raise ContextIntegrityError(
                "estimate_changed_after_hashing",
                "audit hashes changed the provider wire token estimate",
            )
        return PromptBundle(
            model_request=final_request,
            profile=profile.key,
            estimated_input_tokens=after,
            trimming=trimming,
            **hashes,
        )

    def _validate_request(self, request: ContextRequest) -> RunProfile:
        profile = self._registry.get(request.profile.key)
        if canonical_json(profile) != canonical_json(request.profile):
            raise ProfileMismatch("profile_mismatch", "profile is not the registered value")
        if request.step not in {rule.step for rule in profile.inference_rules}:
            raise ProfileMismatch("profile_step_mismatch", "step is not registered for profile")
        expected_scope = {
            "retail": ("retail", "retail"),
            "bird_a": ("bird", "a"),
            "bird_c": ("bird", "c"),
        }[profile.key]
        if (request.run_scope.track, request.run_scope.mode) != expected_scope:
            raise ProfileMismatch("profile_scope_mismatch", "scope does not match profile")
        if self._estimator.revision != profile.estimator_revision:
            raise RevisionMismatch()

        datums: Iterable[ContextDatum] = (
            request.current_input,
            *request.confirmed_facts,
            *request.evidence,
        )
        for datum in datums:
            try:
                validated = ContextDatum.model_validate(datum.model_dump())
            except ValidationError as error:
                raise ContextIntegrityError(
                    "context_integrity_error", "context datum failed integrity validation"
                ) from error
            if validated.namespace not in profile.allowed_namespaces:
                raise DataNamespaceViolation(
                    "data_namespace_violation",
                    "context datum namespace is not allowed for this profile",
                )
        if request.latest_error is not None:
            try:
                type(request.latest_error).model_validate(request.latest_error.model_dump())
            except ValidationError as error:
                raise ContextIntegrityError(
                    "context_integrity_error", "error datum failed integrity validation"
                ) from error

        digest = scope_digest(request.run_scope)
        sequences: list[int] = []
        for group in request.history:
            try:
                validated_group = type(group).model_validate(group.model_dump())
            except ValidationError as error:
                raise ToolResultMismatch(
                    "tool_result_mismatch", "conversation group is not closed"
                ) from error
            ref = validated_group.provider_turn_ref
            if ref.scope_digest != digest or ref.attempt_id != request.attempt_id:
                raise ToolResultMismatch(
                    "tool_result_mismatch", "conversation group belongs to another attempt"
                )
            sequences.append(ref.sequence)
        if sequences != sorted(set(sequences)) or any(
            sequence >= request.sequence for sequence in sequences
        ):
            raise ToolResultMismatch("tool_result_mismatch", "conversation sequence is invalid")
        return profile

    def _messages(
        self,
        request: ContextRequest,
        profile: RunProfile,
        evidence: tuple[ContextDatum, ...],
    ) -> tuple[ChatMessage, ...]:
        common_policy, profile_policy = self._registry.policy_for(profile.key)
        records = [
            _datum_row(request.current_input),
            *(_datum_row(item) for item in request.confirmed_facts),
            *(_datum_row(item) for item in evidence),
        ]
        if request.latest_error is not None:
            records.append(
                {
                    "record_type": "error",
                    "namespace": "runtime_error",
                    "source_ref": request.latest_error.error_type,
                    "revision": None,
                    "digest": request.latest_error.digest,
                    "content": request.latest_error.message,
                    "reason_code": request.latest_error.reason_code,
                    "retryable": request.latest_error.retryable,
                }
            )
        return (
            ChatMessage(role="system", content=common_policy),
            ChatMessage(role="system", content=profile_policy),
            ChatMessage(role="user", content=canonical_json({"records": records})),
        )

    def _model_request(
        self,
        request: ContextRequest,
        profile: RunProfile,
        inference: InferenceConfig,
        history: tuple[ConversationGroup, ...],
        evidence: tuple[ContextDatum, ...],
        config_hash: str,
    ) -> ModelRequest:
        return ModelRequest(
            run_scope=request.run_scope,
            attempt_id=request.attempt_id,
            sequence=request.sequence,
            inference=inference,
            messages=self._messages(request, profile, evidence),
            history=history,
            tools=self._registry.tools_for_step(profile.key, request.step),
            timeout_seconds=self._timeout_seconds,
            capability_revision=profile.capability_revision,
            provider_user_id=self._provider_user_id,
            prompt_policy_hash=_ZERO_HASH,
            rendered_prompt_hash=_ZERO_HASH,
            tool_hash=_ZERO_HASH,
            context_hash=_ZERO_HASH,
            config_hash=config_hash,
        )

    def _estimate(self, candidate: ModelRequest, profile: RunProfile) -> int:
        estimate = self._estimator.estimate(candidate)
        if estimate.estimator_revision != profile.estimator_revision:
            raise RevisionMismatch()
        return estimate.input_tokens

    def _final_request(
        self,
        request: ContextRequest,
        profile: RunProfile,
        inference: InferenceConfig,
        history: tuple[ConversationGroup, ...],
        evidence: tuple[ContextDatum, ...],
        config_hash: str,
        trimming: TrimmingSummary,
    ) -> tuple[ModelRequest, dict[str, str]]:
        provisional = self._model_request(
            request, profile, inference, history, evidence, config_hash
        )
        common_policy, profile_policy = self._registry.policy_for(profile.key)
        records = (
            request.current_input,
            *request.confirmed_facts,
            *evidence,
        )
        hashes = {
            "prompt_policy_hash": compute_prompt_policy_hash(common_policy, profile_policy),
            "rendered_prompt_hash": compute_rendered_prompt_hash(provisional.messages),
            "tool_hash": compute_tool_hash(profile, provisional.tools),
            "context_hash": compute_context_hash(records, history, trimming, request.latest_error),
            "config_hash": config_hash,
        }
        return provisional.model_copy(update=hashes), hashes
