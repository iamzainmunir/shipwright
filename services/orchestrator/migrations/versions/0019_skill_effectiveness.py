"""skill effectiveness metrics — successes/fails drive recall ranking + retirement (A14)

Adds ``skills.successes`` and ``skills.fails``: how many runs that recalled a skill then shipped vs
reworked/halted. Recall ranks by effectiveness and auto-demotes skills that don't help. Plain integer
columns on an existing tenant table — RLS is already in force on ``skills``.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-27
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("skills", sa.Column("successes", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("skills", sa.Column("fails", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("skills", "fails")
    op.drop_column("skills", "successes")
