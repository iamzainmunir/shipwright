"""run provider/model attribution

Adds ``runs.provider`` and ``runs.model`` so per-provider usage is metered from what
each run *actually* executed on (no fabricated per-provider profiles).

(The invented AutonomyPolicy defaults — $1,700 cap / $25 threshold — are now 0 = "unset"
in the model/schema defaults, so fresh installs are honest. Existing rows are reset by the
app via PATCH /settings rather than here: under FORCE ROW LEVEL SECURITY a migration
running as the non-superuser app role, with no ``app.workspace_id`` GUC set, matches no
rows, so a blanket UPDATE here would silently do nothing.)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-22
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("provider", sa.String(length=32), nullable=True))
    op.add_column("runs", sa.Column("model", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "model")
    op.drop_column("runs", "provider")
