"""Wipe ALL workspace data — a clean slate.

Truncates every table (missions, runs, steps, events, blockers, agents, model connections,
skills, memories, integrations, settings) and resets the mission-key sequence. Destructive:
there is no undo.

After running, restart the orchestrator:
  * default (FOUNDRY_SEED unset/1) → the honest baseline fixtures are re-seeded.
  * FOUNDRY_SEED=0                 → the workspace stays completely empty.

Usage (from services/orchestrator):
  DATABASE_URL="postgresql+asyncpg://foundry_app@127.0.0.1:5432/foundry_dev" \\
    uv run python scripts/reset_db.py --yes
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

TABLES = [
    "missions", "runs", "steps", "events", "blockers",
    "agents", "model_connections", "skills", "memories",
    "integrations", "autonomy_policies",
]
MISSION_KEY_START = 151  # matches migration 0001


async def main() -> None:
    if "--yes" not in sys.argv:
        print("Refusing to wipe without --yes. This deletes ALL data. Re-run with --yes.")
        return

    dsn = get_settings().database_url
    engine = create_async_engine(dsn)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        await conn.execute(text(f"ALTER SEQUENCE mission_key_seq RESTART WITH {MISSION_KEY_START}"))
    await engine.dispose()
    print(f"Wiped {len(TABLES)} tables and reset mission_key_seq to {MISSION_KEY_START}.")
    print("Restart the orchestrator: FOUNDRY_SEED=0 for an empty workspace, or default to re-seed the baseline.")


if __name__ == "__main__":
    asyncio.run(main())
