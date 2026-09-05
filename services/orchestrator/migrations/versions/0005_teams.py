"""teams + agent multi-model + mission team assignment

Adds:
  * ``agents.models`` (JSON) — ordered model list for failover (primary + fallbacks).
  * ``missions.team_id`` — the Team staffing a mission.
  * ``teams`` table — a named role→agent group (members JSON: [{agentId, accountable}]), with
    the same FORCE ROW LEVEL SECURITY + ws_isolation policy as every tenant table.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-22
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("models", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column("missions", sa.Column("team_id", sa.String(length=40), nullable=True))
    op.create_table(
        "teams",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("org_id", sa.String(length=40), nullable=True),
        sa.Column("workspace_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("members", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Tenant isolation — identical policy to every other table (Canon §13.2).
    op.execute("ALTER TABLE teams ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE teams FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS ws_isolation ON teams")
    op.execute(
        "CREATE POLICY ws_isolation ON teams "
        "USING (workspace_id = current_setting('app.workspace_id', true)) "
        "WITH CHECK (workspace_id = current_setting('app.workspace_id', true))"
    )


def downgrade() -> None:
    op.drop_table("teams")
    op.drop_column("missions", "team_id")
    op.drop_column("agents", "models")
