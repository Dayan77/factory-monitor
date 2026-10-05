import asyncio
import os
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool
from sqlalchemy.engine import make_url
from alembic import context

from src.models.base import Base
import src.models  # noqa: F401 — ensure all models are registered
from src.config import settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override alembic.ini URL with runtime settings (picks up env vars like POSTGRES_HOST)
config.set_main_option("sqlalchemy.url", settings.database_url)


def _check_no_conflicting_database_url() -> None:
    """The target DB comes from POSTGRES_HOST/PORT/DB (src/config.py). DATABASE_URL is NOT read:
    a DATABASE_URL pointing elsewhere once made a `downgrade base` wipe the dev database instead
    of the intended scratch DB. Refuse to run when they disagree."""
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        return
    want, got = make_url(raw), make_url(settings.database_url)
    same = (want.host, want.port or 5432, want.database) == (got.host, got.port or 5432, got.database)
    if not same:
        raise SystemExit(
            f"DATABASE_URL aponta para {want.host}:{want.port or 5432}/{want.database}, mas as migrações "
            f"usariam {got.host}:{got.port or 5432}/{got.database} (POSTGRES_HOST/PORT/DB). "
            "Defina POSTGRES_DB (e POSTGRES_HOST/PORT) para o banco desejado; DATABASE_URL é ignorada."
        )


_check_no_conflicting_database_url()

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
