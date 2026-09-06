"""notification provider credentials in the DB (configured in the UI, not .env)

Adds ``autonomy_policies.notify_config`` (JSON): SMTP / Twilio / Meta / Slack settings and secrets,
stored per workspace and edited in Settings. Secret keys are redacted on read + merged on write by
the API. Defaults to ``{}`` so existing rows are unaffected; the notifier still falls back to the
legacy env vars when a key is absent.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-06
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "autonomy_policies",
        sa.Column("notify_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("autonomy_policies", "notify_config")
