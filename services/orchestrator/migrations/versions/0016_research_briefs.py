"""research briefs — cited internet-research output from the Researcher role (spec P6)

Adds the ``research_briefs`` table: a workspace-scoped, mission-linked brief with cited findings,
recommendations, and source URLs. Same FORCE ROW LEVEL SECURITY + ws_isolation policy as every tenant
table (Canon §13.2). Per-column indexes on workspace_id + mission_id (matching the ORM) make lookup cheap.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-27
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_briefs",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("org_id", sa.String(length=40), nullable=True),
        sa.Column("workspace_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("mission_id", sa.String(length=40), nullable=True, index=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("recommendations", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("sources", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Tenant isolation — identical policy to every other table.
    op.execute("ALTER TABLE research_briefs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE research_briefs FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS ws_isolation ON research_briefs")
    op.execute(
        "CREATE POLICY ws_isolation ON research_briefs "
        "USING (workspace_id = current_setting('app.workspace_id', true)) "
        "WITH CHECK (workspace_id = current_setting('app.workspace_id', true))"
    )


def downgrade() -> None:
    op.drop_table("research_briefs")
