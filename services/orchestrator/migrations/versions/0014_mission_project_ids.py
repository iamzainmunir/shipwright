"""mission multi-project working set (Approach A)

Adds ``missions.project_ids`` (JSON array of Project ids) — the set of projects a coordinated
change spans. Empty ⇒ single-project behavior via ``project_path``. Defaults to ``[]`` so existing
rows are unaffected.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-06
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "missions",
        sa.Column("project_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("missions", "project_ids")
