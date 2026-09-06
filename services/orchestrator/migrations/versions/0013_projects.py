"""project registry — local-git-repo codebases the org builds in / edits (multi-project)

Adds the ``projects`` table: a registered or auto-built codebase (name + local git repo path),
workspace-scoped, with the same FORCE ROW LEVEL SECURITY + ws_isolation policy as every tenant
table. A unique index on (workspace_id, path) makes built-project upsert idempotent.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-06
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("org_id", sa.String(length=40), nullable=True),
        sa.Column("workspace_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="registered"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_projects_ws_path", "projects", ["workspace_id", "path"], unique=True)
    # Tenant isolation — identical policy to every other table (Canon §13.2).
    op.execute("ALTER TABLE projects ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE projects FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS ws_isolation ON projects")
    op.execute(
        "CREATE POLICY ws_isolation ON projects "
        "USING (workspace_id = current_setting('app.workspace_id', true)) "
        "WITH CHECK (workspace_id = current_setting('app.workspace_id', true))"
    )


def downgrade() -> None:
    op.drop_index("ix_projects_ws_path", table_name="projects")
    op.drop_table("projects")
