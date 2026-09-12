import pytest
from pydantic import ValidationError

from commerce_agent.config import load_settings


def test_load_settings_uses_safe_defaults_without_a_secret() -> None:
    settings = load_settings({})

    assert settings.environment == "development"
    assert str(settings.deepseek_base_url) == "https://api.deepseek.com/"
    assert settings.deepseek_api_key is None


def test_load_settings_rejects_an_unknown_environment() -> None:
    with pytest.raises(ValidationError, match="environment"):
        load_settings({"COMMERCE_AGENT_ENVIRONMENT": "staging"})


def test_load_settings_wraps_product_database_dsn_as_a_secret() -> None:
    raw_dsn = "postgresql://agent_reader:not-a-real-password@127.0.0.1:5432/commerce_analyst"

    settings = load_settings({"PRODUCT_DATABASE_DSN": raw_dsn})

    assert settings.product_database_dsn is not None
    assert settings.product_database_dsn.get_secret_value() == raw_dsn
    assert raw_dsn not in repr(settings)


def test_load_settings_wraps_knowledge_dsn_as_a_secret() -> None:
    raw_dsn = "postgresql://knowledge_reader:not-a-real-password@127.0.0.1:5432/commerce_analyst"

    settings = load_settings({"PRODUCT_KNOWLEDGE_DATABASE_DSN": raw_dsn})

    assert settings.product_knowledge_database_dsn is not None
    assert settings.product_knowledge_database_dsn.get_secret_value() == raw_dsn
    assert raw_dsn not in repr(settings)


def test_runtime_state_dsns_are_independent_secrets() -> None:
    checkpoint_dsn = (
        "postgresql://checkpoint_writer:secret@127.0.0.1:5432/commerce_analyst"
    )
    model_state_dsn = (
        "postgresql://model_state_writer:other@127.0.0.1:5432/commerce_analyst"
    )

    settings = load_settings(
        {
            "PRODUCT_CHECKPOINT_DATABASE_DSN": checkpoint_dsn,
            "PRODUCT_MODEL_STATE_DATABASE_DSN": model_state_dsn,
        }
    )

    assert settings.product_checkpoint_database_dsn is not None
    assert settings.product_checkpoint_database_dsn.get_secret_value() == checkpoint_dsn
    assert settings.product_model_state_database_dsn is not None
    assert settings.product_model_state_database_dsn.get_secret_value() == model_state_dsn
    assert "secret" not in repr(settings)
    assert "other" not in repr(settings)


def test_day4_operation_settings_are_independent_redacted_secrets() -> None:
    environment = {
        "PRODUCT_PROPOSAL_DATABASE_DSN": "postgresql://proposal_writer:proposal-secret@db/name",
        "PRODUCT_APPROVAL_DATABASE_DSN": "postgresql://approval_writer:approval-secret@db/name",
        "PRODUCT_OPERATION_DATABASE_DSN": "postgresql://operation_executor:operation-secret@db/name",
        "PRODUCT_TRACE_DATABASE_DSN": "postgresql://trace_writer:trace-secret@db/name",
        "PRODUCT_OPERATION_APPROVAL_HMAC_KEY_V1": "approval-hmac-secret",
        "PRODUCT_SELLER_REF_HMAC_KEY_V1": "seller-ref-hmac-secret",
    }

    settings = load_settings(environment)

    fields = {
        "PRODUCT_PROPOSAL_DATABASE_DSN": settings.product_proposal_database_dsn,
        "PRODUCT_APPROVAL_DATABASE_DSN": settings.product_approval_database_dsn,
        "PRODUCT_OPERATION_DATABASE_DSN": settings.product_operation_database_dsn,
        "PRODUCT_TRACE_DATABASE_DSN": settings.product_trace_database_dsn,
        "PRODUCT_OPERATION_APPROVAL_HMAC_KEY_V1": (
            settings.product_operation_approval_hmac_key_v1
        ),
        "PRODUCT_SELLER_REF_HMAC_KEY_V1": settings.product_seller_ref_hmac_key_v1,
    }
    for environment_name, field in fields.items():
        assert field is not None
        assert field.get_secret_value() == environment[environment_name]
        assert environment[environment_name] not in repr(settings)
        assert environment[environment_name] not in repr(settings.model_dump())


def test_day4_settings_remain_optional_and_exclude_scenario_reset_capability() -> None:
    settings = load_settings(
        {
            "PRODUCT_SCENARIO_RESET_DATABASE_DSN": (
                "postgresql://product_scenario_reset:private@db/name"
            )
        }
    )

    assert settings.product_proposal_database_dsn is None
    assert settings.product_approval_database_dsn is None
    assert settings.product_operation_database_dsn is None
    assert settings.product_trace_database_dsn is None
    assert settings.product_operation_approval_hmac_key_v1 is None
    assert settings.product_seller_ref_hmac_key_v1 is None
    assert "product_scenario_reset_database_dsn" not in type(settings).model_fields
    assert not hasattr(settings, "product_scenario_reset_database_dsn")
