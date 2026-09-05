"""model_connections.config (usage limits / restrictions)

Adds the JSON ``config`` column that backs per-connection usage limits and restrictions
(Phase 11). NOT NULL with a ``'{}'`` server default so existing rows backfill cleanly.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-22
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_connections",
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("model_connections", "config")
