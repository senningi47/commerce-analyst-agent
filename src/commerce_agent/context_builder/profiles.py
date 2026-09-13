"""Fail-closed loader for the three registered Day 3 run profiles."""

import json
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

from pydantic import TypeAdapter, ValidationError

from commerce_agent.context_builder.contracts import (
    DataNamespace,
    PromptStep,
    RunProfile,
    RunProfileKey,
)
from commerce_agent.model.contracts import ToolDefinition

_FILES = (
    "run-profiles.v2.json",
    "prompt-policies.v2.json",
    "bird-tools.synthetic.v1.json",
)
_PROFILE_ADAPTER = TypeAdapter(RunProfile)
_COMMON_NAMESPACES = {
    DataNamespace.USER_INPUT,
    DataNamespace.CONFIRMED_FACT,
    DataNamespace.RUNTIME_ERROR,
}
_NAMESPACES = {
    RunProfileKey.RETAIL: _COMMON_NAMESPACES
    | {
        DataNamespace.OLIST_SCHEMA,
        DataNamespace.RETAIL_KNOWLEDGE,
        DataNamespace.BUSINESS_VALUE,
        DataNamespace.PRODUCT_QUERY_RESULT,
    },
    RunProfileKey.BIRD_A: _COMMON_NAMESPACES
    | {
        DataNamespace.BIRD_SCHEMA,
        DataNamespace.BIRD_OFFICIAL_FEEDBACK,
        DataNamespace.BIRD_A_TOOL_RESULT,
    },
    RunProfileKey.BIRD_C: _COMMON_NAMESPACES
    | {
        DataNamespace.BIRD_SCHEMA,
        DataNamespace.BIRD_OFFICIAL_FEEDBACK,
        DataNamespace.BIRD_C_PHASE,
    },
}
_STEPS = {
    RunProfileKey.RETAIL: {
        PromptStep.RETAIL_DECIDE,
        PromptStep.RETAIL_CLARIFY,
        PromptStep.RETAIL_REPORT,
        PromptStep.RETAIL_SQL_GENERATE,
        PromptStep.RETAIL_SQL_REPAIR,
    },
    RunProfileKey.BIRD_A: {PromptStep.BIRD_A_ACT},
    RunProfileKey.BIRD_C: {PromptStep.BIRD_C_RESPOND},
}
_POLICY_REVISIONS = {
    "common-envelope-v1",
    "retail-policy-v2",
    "bird-a-policy-v1",
    "bird-c-policy-v1",
}
_FORBIDDEN_SYNTHETIC = (
    "olist",
    "retail",
    # "knowledge" is intentionally absent: the official BIRD actions
    # (get_knowledge_definition et al.) legitimately use the word; the tokens
    # below guard against Product-track vocabulary leaking into BIRD configs.
    "resolver",
    "5432",
    "6002",
    "dsn",
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class RevisionMismatch(RuntimeError):
    """A registered config is absent, noncanonical, or cross-file inconsistent."""

    def __init__(self, message: str = "registered model configuration mismatch") -> None:
        super().__init__(message)
        self.reason_code = "revision_mismatch"
        self.retryable = False


def _load_canonical(path: Path) -> dict[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RevisionMismatch() from error
    if not isinstance(parsed, dict):
        raise RevisionMismatch()
    if raw not in {_canonical_json(parsed), _canonical_json(parsed) + "\n"}:
        raise RevisionMismatch()
    return parsed


def _tool_definition(raw: object, catalog_revision: str) -> ToolDefinition:
    if not isinstance(raw, dict):
        raise RevisionMismatch()
    try:
        name = raw["name"]
        description = raw["description"]
        parameters = raw["parameters"]
        if set(raw) != {"name", "description", "parameters"}:
            raise RevisionMismatch()
        parameters_json = _canonical_json(parameters)
        identity = _canonical_json(
            {
                "catalog_revision": catalog_revision,
                "description": description,
                "name": name,
                "parameters": parameters,
            }
        )
        return ToolDefinition(
            name=name,
            description=description,
            parameters_json=parameters_json,
            catalog_revision=catalog_revision,
            capability_sha256=sha256(identity.encode("utf-8")).hexdigest(),
        )
    except (KeyError, TypeError, ValidationError, ValueError) as error:
        raise RevisionMismatch() from error


class ProfileRegistry:
    """Immutable profiles, policies, and tools selected by a fixed key."""

    def __init__(
        self,
        *,
        profiles: Mapping[RunProfileKey, RunProfile],
        policies: Mapping[str, str],
        tools: Mapping[RunProfileKey, tuple[ToolDefinition, ...]],
    ) -> None:
        self._profiles = MappingProxyType(dict(profiles))
        self._policies = MappingProxyType(dict(policies))
        self._tools = MappingProxyType(dict(tools))

    @classmethod
    def load(cls, config_root: Path) -> "ProfileRegistry":
        documents = {name: _load_canonical(config_root / name) for name in _FILES}
        run_document = documents[_FILES[0]]
        policy_document = documents[_FILES[1]]
        synthetic_document = documents[_FILES[2]]
        if run_document.get("revision") != "run-profiles-v2":
            raise RevisionMismatch()
        if policy_document.get("revision") != "prompt-policies-v2":
            raise RevisionMismatch()
        if synthetic_document.get("revision") != "bird-tools-manifest-v1":
            raise RevisionMismatch()

        policies = cls._parse_policies(policy_document)
        profiles = cls._parse_profiles(run_document)
        tools = cls._parse_tools(run_document, synthetic_document)
        cls._validate_cross_references(profiles, policies, tools)
        return cls(profiles=profiles, policies=policies, tools=tools)

    @staticmethod
    def _parse_policies(document: dict[str, object]) -> dict[str, str]:
        if set(document) != {"revision", "policies"}:
            raise RevisionMismatch()
        raw_policies = document.get("policies")
        if not isinstance(raw_policies, list):
            raise RevisionMismatch()
        policies: dict[str, str] = {}
        for item in raw_policies:
            if not isinstance(item, dict) or set(item) != {"revision", "body"}:
                raise RevisionMismatch()
            revision, body = item.get("revision"), item.get("body")
            if not isinstance(revision, str) or not isinstance(body, str) or not body:
                raise RevisionMismatch()
            if revision in policies:
                raise RevisionMismatch()
            policies[revision] = body
        if set(policies) != _POLICY_REVISIONS:
            raise RevisionMismatch()
        return policies

    @staticmethod
    def _parse_profiles(document: dict[str, object]) -> dict[RunProfileKey, RunProfile]:
        if set(document) != {"revision", "profiles", "retail_catalog"}:
            raise RevisionMismatch()
        raw_profiles = document.get("profiles")
        if not isinstance(raw_profiles, list):
            raise RevisionMismatch()
        profiles: dict[RunProfileKey, RunProfile] = {}
        for raw in raw_profiles:
            try:
                profile = _PROFILE_ADAPTER.validate_python(raw)
            except ValidationError as error:
                raise RevisionMismatch() from error
            if profile.key in profiles:
                raise RevisionMismatch()
            profiles[profile.key] = profile
        if set(profiles) != set(RunProfileKey):
            raise RevisionMismatch()
        return profiles

    @staticmethod
    def _parse_tools(
        run_document: dict[str, object],
        synthetic_document: dict[str, object],
    ) -> dict[RunProfileKey, tuple[ToolDefinition, ...]]:
        retail_catalog = run_document.get("retail_catalog")
        if not isinstance(retail_catalog, dict) or set(retail_catalog) != {
            "catalog_revision",
            "tools",
        }:
            raise RevisionMismatch()
        retail_revision = retail_catalog.get("catalog_revision")
        if not isinstance(retail_revision, str) or not isinstance(
            retail_catalog.get("tools"), list
        ):
            raise RevisionMismatch()

        if set(synthetic_document) != {"revision", "catalog_revision", "profiles"}:
            raise RevisionMismatch()
        synthetic_revision = synthetic_document.get("catalog_revision")
        synthetic_profiles = synthetic_document.get("profiles")
        if not isinstance(synthetic_revision, str) or not isinstance(synthetic_profiles, dict):
            raise RevisionMismatch()
        if set(synthetic_profiles) != {"bird_a", "bird_c"}:
            raise RevisionMismatch()

        tools = {
            RunProfileKey.RETAIL: tuple(
                _tool_definition(item, retail_revision) for item in retail_catalog["tools"]
            ),
            RunProfileKey.BIRD_A: tuple(
                _tool_definition(item, synthetic_revision) for item in synthetic_profiles["bird_a"]
            ),
            RunProfileKey.BIRD_C: tuple(
                _tool_definition(item, synthetic_revision) for item in synthetic_profiles["bird_c"]
            ),
        }
        # Official a/c modes share tool names (ask_user, submit_sql); uniqueness
        # is enforced per profile, not globally across profiles.
        for group in tools.values():
            names = [tool.name for tool in group]
            if len(set(names)) != len(names):
                raise RevisionMismatch()
        synthetic_text = _canonical_json(synthetic_document).lower()
        if "synthetic" not in synthetic_text or any(
            token in synthetic_text for token in _FORBIDDEN_SYNTHETIC
        ):
            raise RevisionMismatch()
        return tools

    @staticmethod
    def _validate_cross_references(
        profiles: Mapping[RunProfileKey, RunProfile],
        policies: Mapping[str, str],
        tools: Mapping[RunProfileKey, tuple[ToolDefinition, ...]],
    ) -> None:
        revisions: set[str] = set()
        for key, profile in profiles.items():
            if profile.revision in revisions:
                raise RevisionMismatch()
            revisions.add(profile.revision)
            if profile.prompt_policy_revision not in policies:
                raise RevisionMismatch()
            if set(profile.allowed_namespaces) != _NAMESPACES[key]:
                raise RevisionMismatch()
            if {rule.step for rule in profile.inference_rules} != _STEPS[key]:
                raise RevisionMismatch()
            catalog_names = {tool.name for tool in tools[key]}
            if any(
                not set(rule.tool_names) <= catalog_names
                for rule in profile.inference_rules
            ):
                raise RevisionMismatch()
            catalog_revisions = {tool.catalog_revision for tool in tools[key]}
            if catalog_revisions != {profile.tool_catalog_revision}:
                raise RevisionMismatch()
            if len(profile.stop_reason_rules) != 6:
                raise RevisionMismatch()

    def get(self, key: RunProfileKey) -> RunProfile:
        try:
            return self._profiles[RunProfileKey(key)]
        except (KeyError, ValueError) as error:
            raise RevisionMismatch("unknown registered profile") from error

    def tools_for(self, key: RunProfileKey) -> tuple[ToolDefinition, ...]:
        normalized = RunProfileKey(key)
        return self._tools[normalized]

    def tools_for_step(
        self, key: RunProfileKey, step: PromptStep
    ) -> tuple[ToolDefinition, ...]:
        tools = self.tools_for(key)
        try:
            allowed = next(
                rule.tool_names
                for rule in self.get(key).inference_rules
                if rule.step == step
            )
        except StopIteration as error:
            raise RevisionMismatch("unknown profile step") from error
        indexed = {tool.name: tool for tool in tools}
        return tuple(indexed[name] for name in allowed)

    def step_tools(self, key: RunProfileKey) -> dict[str, set[str]]:
        return {
            rule.step.value: set(rule.tool_names)
            for rule in self.get(key).inference_rules
        }

    def schema_for(self, key: RunProfileKey, name: str) -> dict[str, object]:
        try:
            tool = next(item for item in self.tools_for(key) if item.name == name)
        except StopIteration as error:
            raise RevisionMismatch("unknown registered tool") from error
        parsed = json.loads(tool.parameters_json)
        if not isinstance(parsed, dict):
            raise RevisionMismatch()
        return parsed

    def tool_names(self, key: RunProfileKey) -> set[str]:
        return {tool.name for tool in self.tools_for(key)}

    def policy_for(self, key: RunProfileKey) -> tuple[str, str]:
        profile = self.get(key)
        return (
            self._policies["common-envelope-v1"],
            self._policies[profile.prompt_policy_revision],
        )
