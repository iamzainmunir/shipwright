"""Migration invariants that hold without a database (the live apply/reverse is gated in CI).

Guards two ways the migrations can silently drift from the code:
  * more than one head (a branch that would need merging), and
  * a tenant table (one with ``workspace_id``) that the initial migration forgot to give an
    RLS policy — the non-negotiable tenant guard (Canon §13.2).
"""

from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory
from app.db_migrate import _ALEMBIC_INI, _MIGRATIONS_DIR

# The tenant tables as of revision 0001, frozen. A future tenant table gets its RLS policy in
# its own migration; head-level coverage is asserted by the DB-backed "RLS & sequence gate" in
# CI — re-deriving this from the live ORM here would wrongly fail the moment the ORM grows.
_TENANT_TABLES_AT_0001 = {
    "missions", "runs", "events", "blockers", "agents",
    "model_connections", "skills", "memories", "integrations", "autonomy_policies",
}


def _script() -> ScriptDirectory:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return ScriptDirectory.from_config(cfg)


def test_single_head() -> None:
    assert len(_script().get_heads()) == 1


def test_rls_covers_tenant_tables_at_0001() -> None:
    initial = _script().get_revision("0001").module
    assert set(initial.RLS_TABLES) == _TENANT_TABLES_AT_0001
