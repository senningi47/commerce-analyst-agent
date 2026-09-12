import pytest

from scripts.bootstrap_product_db import bootstrap_roles


@pytest.mark.parametrize(
    ("admin_dsn", "agent_password", "knowledge_password", "message"),
    [
        ("", "agent", "knowledge", "ADMIN_DSN"),
        ("dsn", "", "knowledge", "AGENT_READER_PASSWORD"),
        ("dsn", "agent", "", "KNOWLEDGE_READER_PASSWORD"),
        ("dsn", "same", "same", "must differ"),
    ],
)
def test_bootstrap_rejects_invalid_secret_inputs_before_connecting(
    admin_dsn: str,
    agent_password: str,
    knowledge_password: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        bootstrap_roles(admin_dsn, agent_password, knowledge_password)
