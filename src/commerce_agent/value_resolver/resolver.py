"""Single public interface for deterministic business-value resolution."""

import hashlib

from commerce_agent.value_resolver._matching import (
    normalize_city,
    normalize_text,
    trigram_dice,
)
from commerce_agent.value_resolver._store import StoredRevision, StoredValue, ValueStore
from commerce_agent.value_resolver.contracts import (
    Confidence,
    MatchMethod,
    ResolutionStatus,
    ValueCandidate,
    ValueDomain,
    ValueResolutionRequest,
    ValueResolutionResult,
)
from commerce_agent.value_resolver.errors import ValueResolutionDataError

_ID_DOMAINS = {ValueDomain.SELLER_ID, ValueDomain.ORDER_ID}
_CITY_DOMAINS = {ValueDomain.CUSTOMER_CITY, ValueDomain.SELLER_CITY}
_MAX_DOMAIN_VALUES = 10_000
_MAX_CANDIDATES = 10
_TRIGRAM_THRESHOLD = 0.35
_MEDIUM_THRESHOLD = 0.70


class BusinessValueResolver:
    def __init__(self, store: ValueStore) -> None:
        self._store = store

    async def resolve(self, request: ValueResolutionRequest) -> ValueResolutionResult:
        if request.domain in _ID_DOMAINS:
            return await self._resolve_id(request)
        return await self._resolve_text(request)

    async def _resolve_id(self, request: ValueResolutionRequest) -> ValueResolutionResult:
        normalized_input = normalize_text(request.raw_text)
        if len(normalized_input) < 6:
            revision = await self._store.load_revision()
            return self._result(
                request.domain,
                normalized_input,
                ResolutionStatus.TOO_BROAD,
                (),
                revision,
            )

        prefix = len(normalized_input) < 32
        lookup = await self._store.lookup_id(
            request.domain,
            normalized_input,
            prefix=prefix,
            limit=11 if prefix else 2,
        )
        if prefix and (lookup.exceeded_limit or len(lookup.values) > _MAX_CANDIDATES):
            return self._result(
                request.domain,
                normalized_input,
                ResolutionStatus.TOO_BROAD,
                (),
                lookup.revision,
            )
        if not lookup.values:
            return self._result(
                request.domain,
                normalized_input,
                ResolutionStatus.NOT_FOUND,
                (),
                lookup.revision,
            )
        if not prefix and (lookup.exceeded_limit or len(lookup.values) != 1):
            raise ValueResolutionDataError(
                "id_cardinality",
                "exact ID lookup returned an invalid number of values",
            )

        method = MatchMethod.PREFIX if prefix else MatchMethod.EXACT
        candidates = tuple(
            self._candidate(request.domain, item, method, Confidence.HIGH)
            for item in lookup.values[:_MAX_CANDIDATES]
        )
        status = ResolutionStatus.AMBIGUOUS if prefix else ResolutionStatus.RESOLVED
        return self._result(
            request.domain,
            normalized_input,
            status,
            candidates,
            lookup.revision,
        )

    async def _resolve_text(self, request: ValueResolutionRequest) -> ValueResolutionResult:
        snapshot = await self._store.load_text_domain(request.domain)
        if len(snapshot.values) > _MAX_DOMAIN_VALUES:
            raise ValueResolutionDataError(
                "domain_cardinality",
                "value domain exceeds the reviewed cardinality limit",
            )

        normalizer = normalize_city if request.domain in _CITY_DOMAINS else normalize_text
        normalized_input = normalizer(request.raw_text)
        stripped_input = request.raw_text.strip()

        exact = tuple(item for item in snapshot.values if item.canonical_value == stripped_input)
        if exact:
            return self._matched_result(
                request.domain,
                normalized_input,
                exact,
                MatchMethod.EXACT,
                snapshot.revision,
            )

        normalized = tuple(
            item for item in snapshot.values if normalizer(item.canonical_value) == normalized_input
        )
        if normalized:
            return self._matched_result(
                request.domain,
                normalized_input,
                normalized,
                MatchMethod.NORMALIZED_EXACT,
                snapshot.revision,
            )

        aliases = tuple(
            alias
            for alias in snapshot.aliases
            if normalize_text(alias.normalized_alias) == normalize_text(request.raw_text)
        )
        if aliases:
            canonical_values = {alias.canonical_value for alias in aliases}
            if len(canonical_values) != 1:
                raise ValueResolutionDataError(
                    "alias_collision",
                    "one normalized alias maps to multiple canonical values",
                )
            alias = aliases[0]
            stored = next(
                (item for item in snapshot.values if item.canonical_value == alias.canonical_value),
                None,
            )
            if stored is None:
                raise ValueResolutionDataError(
                    "alias_target_missing",
                    "an alias references a missing canonical value",
                )
            aliased = StoredValue(
                stored.canonical_value,
                alias.display_label,
                stored.support_count,
            )
            return self._matched_result(
                request.domain,
                normalized_input,
                (aliased,),
                MatchMethod.ALIAS,
                snapshot.revision,
            )

        scored = []
        for item in snapshot.values:
            score = trigram_dice(normalized_input, normalizer(item.canonical_value))
            if score >= _TRIGRAM_THRESHOLD:
                scored.append((score, item))
        scored.sort(
            key=lambda pair: (
                -pair[0],
                -pair[1].support_count,
                pair[1].canonical_value,
            )
        )
        candidates = tuple(
            self._candidate(
                request.domain,
                item,
                MatchMethod.TRIGRAM,
                Confidence.MEDIUM if score >= _MEDIUM_THRESHOLD else Confidence.LOW,
            )
            for score, item in scored[:_MAX_CANDIDATES]
        )
        status = ResolutionStatus.AMBIGUOUS if candidates else ResolutionStatus.NOT_FOUND
        return self._result(
            request.domain,
            normalized_input,
            status,
            candidates,
            snapshot.revision,
        )

    def _matched_result(
        self,
        domain: ValueDomain,
        normalized_input: str,
        values: tuple[StoredValue, ...],
        method: MatchMethod,
        revision: StoredRevision,
    ) -> ValueResolutionResult:
        ordered = sorted(values, key=lambda item: (-item.support_count, item.canonical_value))
        candidates = tuple(
            self._candidate(domain, item, method, Confidence.HIGH)
            for item in ordered[:_MAX_CANDIDATES]
        )
        status = ResolutionStatus.RESOLVED if len(ordered) == 1 else ResolutionStatus.AMBIGUOUS
        return self._result(
            domain,
            normalized_input,
            status,
            candidates,
            revision,
        )

    @staticmethod
    def _candidate(
        domain: ValueDomain,
        value: StoredValue,
        method: MatchMethod,
        confidence: Confidence,
    ) -> ValueCandidate:
        digest = hashlib.sha256(value.canonical_value.encode("utf-8")).hexdigest()[:16]
        return ValueCandidate(
            canonical_value=value.canonical_value,
            display_label=value.display_label,
            match_method=method,
            confidence=confidence,
            support_count=value.support_count,
            evidence_ref=f"value:{domain.value}:{digest}",
        )

    @staticmethod
    def _result(
        domain: ValueDomain,
        normalized_input: str,
        status: ResolutionStatus,
        candidates: tuple[ValueCandidate, ...],
        revision: StoredRevision,
    ) -> ValueResolutionResult:
        return ValueResolutionResult(
            status=status,
            domain=domain,
            normalized_input=normalized_input,
            candidates=candidates,
            data_manifest_sha256=revision.data_manifest_sha256,
            catalog_revision=revision.catalog_revision,
        )
