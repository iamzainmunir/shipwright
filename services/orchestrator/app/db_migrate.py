"""Programmatic Alembic runner (plan 11 §11.4).

The canonical way to create/upgrade the schema is ``uv run alembic upgrade head`` — run in the
deploy pipeline and gated in CI. For zero-config local development, the app can also bring the
schema to head at startup (see :meth:`app.pgstore.PostgresStore.setup`). Both paths use the same
``migrations/`` revisions and the same ``env.py``, so there is a single schema source of truth
(the ORM-derived migrations) — never ``create_all``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

_ORCHESTRATOR_ROOT = Path(__file__).resolve().parents[1]  # services/orchestrator
_ALEMBIC_INI = _ORCHESTRATOR_ROOT / "alembic.ini"
_MIGRATIONS_DIR = _ORCHESTRATOR_ROOT / "migrations"


def _config(dsn: str) -> Config:
    """An Alembic Config pinned to this service's files and the given database URL."""
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", dsn)
    return cfg


def _upgrade(dsn: str, revision: str) -> None:
    command.upgrade(_config(dsn), revision)


async def upgrade_to_head(dsn: str) -> None:
    """Bring ``dsn`` to the latest revision.

    Alembic's command API is synchronous and our async ``env.py`` calls ``asyncio.run`` inside
    it, so we run the whole command in a worker thread — that gives it its own event loop and
    keeps this safe to call from within the app's running loop.
    """
    await asyncio.to_thread(_upgrade, dsn, "head")
