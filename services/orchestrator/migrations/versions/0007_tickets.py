"""built-in ticket board: tickets + ticket_events + ticket_links (v2 Phase 5)

Adds Shipwright's first-class ticket system (plan 05): one canonical model, rendered as a Jira-like
board in the dashboard and (optionally, Phase 6) mirrored to Jira. Epics map to missions, Stories
to build subtasks, Bugs to QA findings. Also adds ``autonomy_policies.features`` (JSON) so the
board is toggleable in Settings → Features (default on).

Every table carries the same FORCE ROW LEVEL SECURITY + ws_isolation policy as every tenant table.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-28
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS ws_isolation ON {table}")
    op.execute(
        f"CREATE POLICY ws_isolation ON {table} "
        "USING (workspace_id = current_setting('app.workspace_id', true)) "
        "WITH CHECK (workspace_id = current_setting('app.workspace_id', true))"
    )


def upgrade() -> None:
    op.add_column(
        "autonomy_policies",
        sa.Column("features", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )

    op.create_table(
        "tickets",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("workspace_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("key", sa.String(length=40), nullable=False, index=True),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("parent_id", sa.String(length=40), nullable=True, index=True),
        sa.Column("mission_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("subtask_id", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.String(length=40), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="todo", index=True),
        sa.Column("agent_name", sa.String(length=80), nullable=True),
        sa.Column("agent_role", sa.String(length=16), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("priority", sa.String(length=8), nullable=True),
        sa.Column("reopen_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("jira_key", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False, index=True),
        sa.UniqueConstraint("workspace_id", "key", name="uq_tickets_ws_key"),
    )
    op.create_table(
        "ticket_events",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("workspace_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("ticket_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=True),
        sa.Column("to_status", sa.String(length=16), nullable=True),
        sa.Column("actor_name", sa.String(length=80), nullable=True),
        sa.Column("actor_role", sa.String(length=16), nullable=True),
        sa.Column("body", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False, index=True),
    )
    op.create_table(
        "ticket_links",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("workspace_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("from_ticket", sa.String(length=40), nullable=False, index=True),
        sa.Column("to_ticket", sa.String(length=40), nullable=False, index=True),
        sa.Column("link_type", sa.String(length=12), nullable=False, server_default="relates"),
    )
    for table in ("tickets", "ticket_events", "ticket_links"):
        _enable_rls(table)

    # Human-facing ticket keys (FT-101, …) come from a dedicated sequence, like mission keys.
    op.execute("CREATE SEQUENCE IF NOT EXISTS ticket_key_seq START WITH 101")


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS ticket_key_seq")
    op.drop_table("ticket_links")
    op.drop_table("ticket_events")
    op.drop_table("tickets")
    op.drop_column("autonomy_policies", "features")
