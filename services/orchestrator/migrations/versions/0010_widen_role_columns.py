"""widen role-bearing columns to fit custom-role slugs (v2)

Custom roles (0009) use a slug key up to 48 chars (e.g. "product-coordinator" = 19), but the
columns that store an agent's role — agents.role_key and the *_role trail columns — were still
VARCHAR(16) from when role keys were the short built-in enum values. Assigning a custom role to an
agent therefore blew up with a StringDataRightTruncation. Widen them all to 48 to match
custom_roles.key.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column) pairs that store an agent/actor role slug.
_ROLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("agents", "role_key"),
    ("tickets", "agent_role"),
    ("ticket_events", "actor_role"),
    ("steps", "agent_role"),
    ("events", "agent_role"),
)


def upgrade() -> None:
    for table, column in _ROLE_COLUMNS:
        op.alter_column(table, column, type_=sa.String(length=48), existing_type=sa.String(length=16))


def downgrade() -> None:
    # Truncates any slug longer than 16; only the built-in keys survive round-trip.
    for table, column in _ROLE_COLUMNS:
        op.alter_column(table, column, type_=sa.String(length=16), existing_type=sa.String(length=48))
