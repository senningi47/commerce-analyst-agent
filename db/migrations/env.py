"""Alembic runtime configuration for the product database."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

admin_dsn = os.environ["PRODUCT_POSTGRES_ADMIN_DSN"]
sqlalchemy_dsn = admin_dsn.replace("postgresql://", "postgresql+psycopg://", 1)
config.set_main_option("sqlalchemy.url", sqlalchemy_dsn.replace("%", "%%"))
target_metadata = None


def run_migrations_offline() -> None:
    raise RuntimeError("Offline migrations are disabled for this local project")


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
