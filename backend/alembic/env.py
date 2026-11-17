import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import dotenv_values
from sqlalchemy import engine_from_config, pool

from app.core.config import Settings, get_settings
from app.models.domain import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return Settings.normalize_database_url(url)
    env_url = os.environ.get("DATABASE_URL")
    if not env_url:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        if env_path.exists():
            env_url = dotenv_values(env_path).get("DATABASE_URL")
    if env_url:
        return Settings.normalize_database_url(str(env_url))
    return get_settings().DATABASE_URL


def run_migrations_offline() -> None:
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    if not configuration.get("sqlalchemy.url"):
        configuration["sqlalchemy.url"] = get_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
