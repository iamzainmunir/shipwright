"""per-workspace notification preferences (email / WhatsApp / Slack) (v2)

Adds opt-in notification preferences to ``autonomy_policies``. Provider credentials live in the
environment (never the DB); only the non-secret preferences are stored here:

  * ``notify_enabled``  — master on/off.
  * ``notify_channels`` — {email, whatsapp_twilio, whatsapp_meta, slack} → bool.
  * ``notify_events``   — {blocker, completed, failed, approval} → bool.
  * ``notify_email``    — recipient email address (email channel).
  * ``notify_whatsapp`` — recipient WhatsApp number, E.164 (WhatsApp channels).

All default to off/empty so existing rows are unaffected.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-06
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "autonomy_policies",
        sa.Column("notify_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "autonomy_policies",
        sa.Column("notify_channels", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "autonomy_policies",
        sa.Column("notify_events", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "autonomy_policies",
        sa.Column("notify_email", sa.String(length=320), nullable=False, server_default=""),
    )
    op.add_column(
        "autonomy_policies",
        sa.Column("notify_whatsapp", sa.String(length=32), nullable=False, server_default=""),
    )


def downgrade() -> None:
    for col in ("notify_whatsapp", "notify_email", "notify_events", "notify_channels", "notify_enabled"):
        op.drop_column("autonomy_policies", col)
