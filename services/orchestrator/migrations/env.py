"""Alembic environment — async (SQLAlchemy 2.0 + asyncpg).

The schema source of truth is the ORM metadata in :mod:`app.db_models`, so autogenerate
diffs future model changes into new revisions. The database URL comes from the app Settings
(``DATABASE_URL``); a programmatic caller (see :mod:`app.db_migrate`) may override it by setting
the ``sqlalchemy.url`` main option before invoking a command.
"""

from __future__ import annotations

import asyncio

from alembic import context
from app.config import get_settings
from app.db_models import Base
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    """Explicit override (set by app.db_migrate) wins; otherwise the app Settings DSN."""
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL without a DBAPI connection (``alembic upgrade --sql``)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# Fixed key for the transaction-level advisory lock that serializes concurrent runners
# (multiple app replicas auto-migrating at once, or a deploy job racing a pod). The second
# holder waits, then finds the DB already at head and does nothing.
_MIGRATION_LOCK_KEY = 4242042


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        connection.exec_driver_sql(f"SELECT pg_advisory_xact_lock({_MIGRATION_LOCK_KEY})")
        context.run_migrations()


async def _run_async_migrations() -> None:
    config.set_main_option("sqlalchemy.url", _database_url())
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=NullPool
    )
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
