import pytest
from pydantic import ValidationError

from commerce_agent.query_engine.contracts import QueryRequest, QueryResult


def test_query_request_rejects_blank_sql() -> None:
    with pytest.raises(ValidationError):
        QueryRequest(sql="")


def test_query_result_is_immutable() -> None:
    result = QueryResult(columns=["order_status"], rows=[{"order_status": "delivered"}])

    assert result.row_count == 1
    assert result.truncated is False
    with pytest.raises(ValidationError):
        result.rows = []
