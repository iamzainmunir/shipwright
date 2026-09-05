"""mission app-builder fields

Adds ``project_kind`` (app | change | null=legacy demo), ``project_path`` (where the app is
built/edited), and ``requirements`` (the full brief, e.g. extracted from an uploaded .docx/.md)
so a mission can drive a real, greenfield build instead of the hardcoded demo task.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-22
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("missions", sa.Column("project_kind", sa.String(length=16), nullable=True))
    op.add_column("missions", sa.Column("project_path", sa.Text(), nullable=True))
    op.add_column("missions", sa.Column("requirements", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("missions", "requirements")
    op.drop_column("missions", "project_path")
    op.drop_column("missions", "project_kind")
