"""custom_roles: user-defined agent roles with default skills (v2)

Lets a workspace define its own agent roles beyond the built-in catalog (e.g. Product Coordinator),
each with a default skill set applied to agents created with it — like a real org adding roles.
Same FORCE ROW LEVEL SECURITY + ws_isolation policy as every tenant table.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-31
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "custom_roles",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("workspace_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("key", sa.String(length=48), nullable=False, index=True),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("group", sa.String(length=16), nullable=False, server_default="other"),
        sa.Column("skills", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("scope", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workspace_id", "key", name="uq_custom_roles_ws_key"),
    )
    op.execute("ALTER TABLE custom_roles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE custom_roles FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS ws_isolation ON custom_roles")
    op.execute(
        "CREATE POLICY ws_isolation ON custom_roles "
        "USING (workspace_id = current_setting('app.workspace_id', true)) "
        "WITH CHECK (workspace_id = current_setting('app.workspace_id', true))"
    )


def downgrade() -> None:
    op.drop_table("custom_roles")
