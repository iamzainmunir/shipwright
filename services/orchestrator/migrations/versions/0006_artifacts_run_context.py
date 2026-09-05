"""artifacts table + durable run context (v2 Phase 0)

Adds:
  * ``runs.context`` (JSON) — ground-truth run context (build_facts, merge meta, qa_passed)
    persisted so restarts never lose the health gate's evidence.
  * ``artifacts`` table — evidence/deliverable files produced by a run (screenshots, videos,
    QA reports, logs, traces). Files live on disk; rows carry path + sha256 + meta. Same
    FORCE ROW LEVEL SECURITY + ws_isolation policy as every tenant table.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("workspace_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("mission_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("run_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("step_id", sa.String(length=40), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False, server_default="other"),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("mime", sa.String(length=100), nullable=False, server_default="application/octet-stream"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Tenant isolation — identical policy to every other table (Canon §13.2).
    op.execute("ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE artifacts FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS ws_isolation ON artifacts")
    op.execute(
        "CREATE POLICY ws_isolation ON artifacts "
        "USING (workspace_id = current_setting('app.workspace_id', true)) "
        "WITH CHECK (workspace_id = current_setting('app.workspace_id', true))"
    )


def downgrade() -> None:
    op.drop_table("artifacts")
    op.drop_column("runs", "context")
