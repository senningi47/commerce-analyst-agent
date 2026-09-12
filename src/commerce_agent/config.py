"""Validated configuration loading for the application process."""

from collections.abc import Mapping
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, SecretStr


class AppSettings(BaseModel, frozen=True):
    """Settings required by the application, without reading process state directly."""

    environment: Literal["development", "test", "production"]
    deepseek_base_url: AnyHttpUrl
    deepseek_api_key: SecretStr | None = None
    product_database_dsn: SecretStr | None = None
    product_knowledge_database_dsn: SecretStr | None = None
    product_checkpoint_database_dsn: SecretStr | None = None
    product_model_state_database_dsn: SecretStr | None = None
    product_proposal_database_dsn: SecretStr | None = None
    product_approval_database_dsn: SecretStr | None = None
    product_operation_database_dsn: SecretStr | None = None
    product_trace_database_dsn: SecretStr | None = None
    product_operation_approval_hmac_key_v1: SecretStr | None = None
    product_seller_ref_hmac_key_v1: SecretStr | None = None


def load_settings(environment: Mapping[str, str]) -> AppSettings:
    """Build settings from an explicit environment mapping.

    Passing the mapping makes configuration deterministic in tests and keeps
    the API key optional until the real DeepSeek adapter is introduced.
    """

    api_key = environment.get("DEEPSEEK_API_KEY")
    product_database_dsn = environment.get("PRODUCT_DATABASE_DSN")
    product_knowledge_database_dsn = environment.get("PRODUCT_KNOWLEDGE_DATABASE_DSN")
    product_checkpoint_database_dsn = environment.get("PRODUCT_CHECKPOINT_DATABASE_DSN")
    product_model_state_database_dsn = environment.get("PRODUCT_MODEL_STATE_DATABASE_DSN")
    product_proposal_database_dsn = environment.get("PRODUCT_PROPOSAL_DATABASE_DSN")
    product_approval_database_dsn = environment.get("PRODUCT_APPROVAL_DATABASE_DSN")
    product_operation_database_dsn = environment.get("PRODUCT_OPERATION_DATABASE_DSN")
    product_trace_database_dsn = environment.get("PRODUCT_TRACE_DATABASE_DSN")
    product_operation_approval_hmac_key_v1 = environment.get(
        "PRODUCT_OPERATION_APPROVAL_HMAC_KEY_V1"
    )
    product_seller_ref_hmac_key_v1 = environment.get(
        "PRODUCT_SELLER_REF_HMAC_KEY_V1"
    )
    return AppSettings(
        environment=environment.get("COMMERCE_AGENT_ENVIRONMENT", "development"),
        deepseek_base_url=environment.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_api_key=SecretStr(api_key) if api_key else None,
        product_database_dsn=(SecretStr(product_database_dsn) if product_database_dsn else None),
        product_knowledge_database_dsn=(
            SecretStr(product_knowledge_database_dsn) if product_knowledge_database_dsn else None
        ),
        product_checkpoint_database_dsn=(
            SecretStr(product_checkpoint_database_dsn) if product_checkpoint_database_dsn else None
        ),
        product_model_state_database_dsn=(
            SecretStr(product_model_state_database_dsn)
            if product_model_state_database_dsn
            else None
        ),
        product_proposal_database_dsn=(
            SecretStr(product_proposal_database_dsn)
            if product_proposal_database_dsn
            else None
        ),
        product_approval_database_dsn=(
            SecretStr(product_approval_database_dsn)
            if product_approval_database_dsn
            else None
        ),
        product_operation_database_dsn=(
            SecretStr(product_operation_database_dsn)
            if product_operation_database_dsn
            else None
        ),
        product_trace_database_dsn=(
            SecretStr(product_trace_database_dsn) if product_trace_database_dsn else None
        ),
        product_operation_approval_hmac_key_v1=(
            SecretStr(product_operation_approval_hmac_key_v1)
            if product_operation_approval_hmac_key_v1
            else None
        ),
        product_seller_ref_hmac_key_v1=(
            SecretStr(product_seller_ref_hmac_key_v1)
            if product_seller_ref_hmac_key_v1
            else None
        ),
    )
