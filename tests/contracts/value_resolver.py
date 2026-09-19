from commerce_agent.value_resolver.contracts import (
    ResolutionStatus,
    ValueResolutionResult,
)


def assert_result_contract(result: ValueResolutionResult) -> None:
    assert len(result.candidates) <= 10
    assert len(result.data_manifest_sha256) == 64
    assert result.catalog_revision
    if result.status is ResolutionStatus.RESOLVED:
        assert len(result.candidates) == 1
    elif result.status is ResolutionStatus.AMBIGUOUS:
        assert 1 <= len(result.candidates) <= 10
    else:
        assert result.candidates == ()
