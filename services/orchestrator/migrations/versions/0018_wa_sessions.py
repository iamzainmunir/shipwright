"""whatsapp conversation sessions — per-sender control-plane state (P4)

Adds ``wa_sessions``: a per-sender WhatsApp session (state + pending context) so free-text messages
are stateful (start → confirm, ask → answer). Workspace-scoped with the same FORCE ROW LEVEL SECURITY
+ ws_isolation policy as every tenant table (Canon §13.2).

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-27
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wa_sessions",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("org_id", sa.String(length=40), nullable=True),
        sa.Column("workspace_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("sender", sa.String(length=64), nullable=False, index=True),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="idle"),
        sa.Column("context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("ALTER TABLE wa_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE wa_sessions FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS ws_isolation ON wa_sessions")
    op.execute(
        "CREATE POLICY ws_isolation ON wa_sessions "
        "USING (workspace_id = current_setting('app.workspace_id', true)) "
        "WITH CHECK (workspace_id = current_setting('app.workspace_id', true))"
    )


def downgrade() -> None:
    op.drop_table("wa_sessions")
