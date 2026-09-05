-- =============================================================================
-- Shipwright — migration 0002: mission-key sequencing (FND-<n> per workspace)
-- =============================================================================
-- Implements per-workspace mission-key sequencing: mission keys are `FND-<n>`,
-- backed by a `mission_key_counters` table (with a Redis seq backstop in later
-- phases). Depends on 0001_init.sql (ulid domain, workspaces table).
--
-- Redis holds the fast counter (seq:missionkey:{workspace_id}); this table is the
-- DURABLE backstop so keys survive a Redis flush and stay monotonic per workspace.
-- next_mission_key() is the single source of truth used by the DB path / seeds.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + CREATE OR REPLACE FUNCTION.
-- =============================================================================

-- Per-workspace FND-<n> counter. `next_value` is the number the NEXT key gets.
CREATE TABLE IF NOT EXISTS mission_key_counters (
  workspace_id ulid PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
  next_value   bigint NOT NULL DEFAULT 1 CHECK (next_value >= 1)
);

-- Atomically allocate the next mission key for a workspace and return it.
-- Uses UPDATE … RETURNING which takes a row-level lock, so concurrent callers
-- serialize on the counter row and never collide (Acceptance §14.7).
--
--   SELECT next_mission_key('01JBWFND0000000000000000W0');  -- → 'FND-147'
--
CREATE OR REPLACE FUNCTION next_mission_key(p_workspace ulid) RETURNS text AS $$
DECLARE
  n bigint;
BEGIN
  -- Ensure a counter row exists for this workspace (first key = FND-1).
  INSERT INTO mission_key_counters (workspace_id, next_value)
       VALUES (p_workspace, 1)
  ON CONFLICT (workspace_id) DO NOTHING;

  -- Take the current value, then advance the counter (locks the row).
  UPDATE mission_key_counters
     SET next_value = next_value + 1
   WHERE workspace_id = p_workspace
  RETURNING next_value - 1 INTO n;

  IF n IS NULL THEN
    RAISE EXCEPTION 'next_mission_key: unknown workspace %', p_workspace
      USING ERRCODE = 'foreign_key_violation';
  END IF;

  RETURN 'FND-' || n::text;
END; $$ LANGUAGE plpgsql;

-- =============================================================================
-- End 0002_functions.sql
-- =============================================================================
