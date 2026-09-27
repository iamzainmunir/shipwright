"""spec contract + feedback ledger — the single source of truth for spec/dev/QA/review (P1)

Adds ``contracts`` (a mission's versioned UI+API contract) and ``contract_feedback`` (structured
findings against a contract item). Both workspace-scoped with the same FORCE ROW LEVEL SECURITY +
ws_isolation policy as every tenant table (Canon §13.2).

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-27
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _force_rls(table: str) -> None:
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
        "contracts",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("org_id", sa.String(length=40), nullable=True),
        sa.Column("workspace_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("mission_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("items", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    _force_rls("contracts")

    op.create_table(
        "contract_feedback",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("org_id", sa.String(length=40), nullable=True),
        sa.Column("workspace_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("contract_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("item_id", sa.String(length=80), nullable=False),
        sa.Column("run_id", sa.String(length=40), nullable=True),
        sa.Column("phase", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("expected", sa.Text(), nullable=False, server_default=""),
        sa.Column("actual", sa.Text(), nullable=False, server_default=""),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="blocking"),
        sa.Column("feedback", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    _force_rls("contract_feedback")


def downgrade() -> None:
    op.drop_table("contract_feedback")
    op.drop_table("contracts")
