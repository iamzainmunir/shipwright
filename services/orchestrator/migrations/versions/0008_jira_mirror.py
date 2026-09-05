"""Jira mirror: jira_issue_map + jira_outbox (v2 Phase 6)

The optional Jira mirror (plan 05 §4-6). Ticket actions enqueue ``jira_outbox`` rows in the same
store transaction as the ticket write; a per-workspace worker drains them serially with retry/backoff
so no pipeline state ever depends on Jira (Rule 0). ``jira_issue_map`` maps a Shipwright entity to its
Jira key with a UNIQUE constraint that prevents double-creates across retries/crashes.

Same FORCE ROW LEVEL SECURITY + ws_isolation policy as every tenant table.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-28
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
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
    op.create_table(
        "jira_issue_map",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("workspace_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("entity_type", sa.String(length=12), nullable=False),
        sa.Column("entity_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("jira_key", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workspace_id", "entity_type", "entity_id", name="uq_jira_map_entity"),
    )
    op.create_table(
        "jira_outbox",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("workspace_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("idempotency_key", sa.String(length=80), nullable=False, index=True),
        sa.Column("ticket_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("op", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="pending", index=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False, index=True),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_jira_outbox_idem"),
    )
    for table in ("jira_issue_map", "jira_outbox"):
        _enable_rls(table)


def downgrade() -> None:
    op.drop_table("jira_outbox")
    op.drop_table("jira_issue_map")
