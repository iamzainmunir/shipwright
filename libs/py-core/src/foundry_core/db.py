"""Async SQLAlchemy engine factory and the per-request tenant scope (Canon §13.2).

Row-Level Security keys off two Postgres GUCs, set once at the start of every transaction
so they reset at COMMIT/ROLLBACK and never leak across pooled connections:

    app.org_id        = '<ulid>'
    app.workspace_id  = '<ulid>'

:func:`tenant_scope` issues both via ``set_config(key, value, is_local => true)`` — the
parameterizable equivalent of ``SET LOCAL`` (avoids string interpolation into SQL). Use it
inside a request:

    engine = create_engine(settings.database_url)
    Session = create_session_factory(engine)
    async with Session() as session:
        async with tenant_scope(session, workspace_id, org_id):
            rows = (await session.execute(select(missions))).scalars().all()
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

__all__ = ["create_engine", "create_session_factory", "tenant_scope"]


def create_engine(url: str, *, echo: bool = False, **kwargs: object) -> AsyncEngine:
    """Create an async SQLAlchemy engine.

    ``url`` is a SQLAlchemy async URL, e.g.
    ``postgresql+psycopg://user:pass@host:5432/db`` (psycopg3) or
    ``postgresql+asyncpg://...``. ``pool_pre_ping`` guards against stale pooled connections.
    """
    return create_async_engine(url, echo=echo, pool_pre_ping=True, future=True, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return an ``async_sessionmaker`` bound to ``engine`` (expire_on_commit off)."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def tenant_scope(
    session: AsyncSession, workspace_id: str, org_id: str
) -> AsyncIterator[AsyncSession]:
    """Set the RLS tenant GUCs for the duration of the current transaction.

    Both GUCs are set transaction-locally (``is_local => true``). If ``session`` is not
    already in a transaction, one is opened and committed/rolled back around the block so
    the settings have a transaction to live in; if a transaction is already active, this
    joins it and leaves commit/rollback to the caller.
    """
    manage_txn = not session.in_transaction()
    if manage_txn:
        await session.begin()
    # Order: org first, then workspace (both read by every RLS policy — Canon §13.2).
    await session.execute(
        text("SELECT set_config('app.org_id', :val, true)"), {"val": org_id}
    )
    await session.execute(
        text("SELECT set_config('app.workspace_id', :val, true)"), {"val": workspace_id}
    )
    try:
        yield session
    except Exception:
        if manage_txn:
            await session.rollback()
        raise
    else:
        if manage_txn:
            await session.commit()
