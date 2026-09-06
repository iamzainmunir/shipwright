"""per-workspace configurable mission-key prefix + projects directory (v2)

Adds two user-editable Settings fields to ``autonomy_policies`` so a workspace can override the
global defaults from the Settings screen:

  * ``mission_key_prefix`` — e.g. "M" ⇒ mission keys ``M-151``; empty ⇒ the global default
    (``SHIPWRIGHT_MISSION_PREFIX``, "M").
  * ``projects_dir`` — where greenfield apps are built on disk; empty ⇒ ``~/ShipwrightProjects``.

Both default to "" (empty) so existing rows keep the global-default behaviour with no data change.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-06
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "autonomy_policies",
        sa.Column("mission_key_prefix", sa.String(length=16), nullable=False, server_default=""),
    )
    op.add_column(
        "autonomy_policies",
        sa.Column("projects_dir", sa.String(length=512), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("autonomy_policies", "projects_dir")
    op.drop_column("autonomy_policies", "mission_key_prefix")
