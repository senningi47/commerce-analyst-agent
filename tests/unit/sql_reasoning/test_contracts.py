from hashlib import sha256
from uuid import UUID

import pytest
from pydantic import ValidationError

from commerce_agent.context_builder.contracts import ContextDatum
from commerce_agent.model.contracts import RunScope
from commerce_agent.sql_reasoning.contracts import ProductDbError, SqlReasoningRequest


def test_sql_reasoning_request_caps_repairs_at_three_and_errors_are_sanitized() -> None:
    content = "Count delivered orders."
    with pytest.raises(ValidationError):
        SqlReasoningRequest(
            run_scope=RunScope(
                run_id=UUID(int=1),
                track="retail",
                mode="retail",
                subject_id="day4",
                experiment_id="product",
                config_hash="a" * 64,
            ),
            attempt_id=UUID(int=2),
            sequence=0,
            step_id="baseline",
            current_input=ContextDatum(
                kind="user_input",
                namespace="user_input",
                source_ref="request:1",
                content=content,
                digest=sha256(content.encode()).hexdigest(),
            ),
            profile_key="retail",
            repair_number=4,
            latest_error=ProductDbError(
                source="postgres",
                reason_code="undefined_column",
                retryable=True,
                evidence_digest="b" * 64,
            ),
        )


def test_product_db_error_rejects_raw_database_detail() -> None:
    with pytest.raises(ValidationError):
        ProductDbError.model_validate(
            {
                "source": "postgres",
                "reason_code": "undefined_column",
                "retryable": True,
                "evidence_digest": "b" * 64,
                "message": "postgresql://writer:secret@localhost/db",
            }
        )
