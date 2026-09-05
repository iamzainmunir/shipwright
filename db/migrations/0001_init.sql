-- =============================================================================
-- Shipwright — initial database migration (Phase 0 core subset)
-- =============================================================================
-- Target: PostgreSQL 16 + pgvector 0.7+  (see docs/VERSIONS.md)
--
-- The complete DDL-level data layer covers every entity, index, RLS policy, and
-- the ClickHouse/Redis/S3 layout. This file scaffolds ONLY the Phase-0 core subset
-- needed to boot the orchestrator/web:
--   orgs, users, workspaces, memberships, repos, model_connections, agents,
--   skills, missions, blockers, memories.
-- Later migrations add the remaining tables (runs, steps, events, approvals,
-- models, usage_*, defects, sandboxes, …) exactly as defined in doc 02.
--
-- Conventions (Canon §8 / §13):
--   * IDs are ULIDs stored in the `ulid` domain over char(26).
--   * DB is snake_case; timestamps are timestamptz suffixed `_at`.
--   * Tenant GUCs are `app.workspace_id` / `app.org_id` (Canon §13.2); every
--     RLS policy reads exactly those two settings.
--
-- This migration is idempotent-ish: safe to re-run on an existing database
-- (guarded CREATE TYPE / CREATE TABLE IF NOT EXISTS / DROP+CREATE for policies).
-- =============================================================================

-- ---- 1. Extensions ----------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_bytes() for the ULID fallback
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector — memories.embedding vector(1536)
CREATE EXTENSION IF NOT EXISTS citext;     -- case-insensitive email / slug uniqueness

-- ---- 2. ULID domain ---------------------------------------------------------
-- 26-char Crockford base32 (excludes I, L, O, U). See doc 02 §1.
DO $$ BEGIN
  CREATE DOMAIN ulid AS char(26)
    CHECK (VALUE ~ '^[0-9A-HJKMNP-TV-Z]{26}$');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ---- 3. Shared functions ----------------------------------------------------
-- DB-side ULID generator (fallback for seeds/triggers; the app uses libs/py-core).
CREATE OR REPLACE FUNCTION gen_ulid() RETURNS ulid AS $$
DECLARE
  alphabet text := '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
  ts       bigint := (extract(epoch from clock_timestamp()) * 1000)::bigint;
  out      text := '';
  i        int;
  rnd      bytea := gen_random_bytes(10);
BEGIN
  -- 48-bit timestamp → 10 chars
  FOR i IN REVERSE 9..0 LOOP
    out := substr(alphabet, (ts % 32)::int + 1, 1) || out;
    ts := ts / 32;
    EXIT WHEN i = 0 AND length(out) >= 10;
  END LOOP;
  out := lpad(out, 10, '0');
  -- 80-bit randomness → 16 chars
  FOR i IN 0..15 LOOP
    out := out || substr(alphabet, (get_byte(rnd, i % 10) % 32) + 1, 1);
  END LOOP;
  RETURN out::ulid;
END; $$ LANGUAGE plpgsql;

-- updated_at maintenance (attached to every mutable table below).
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

-- Tenant-context accessors (Canon §13.2). Read the per-transaction GUCs the app
-- sets via `SET LOCAL app.workspace_id = '…'` / `SET LOCAL app.org_id = '…'`.
-- Unset context → NULL → RLS matches no rows (safe default).
CREATE OR REPLACE FUNCTION current_org_id() RETURNS ulid
  LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('app.org_id', true), '')::ulid $$;

CREATE OR REPLACE FUNCTION current_workspace_id() RETURNS ulid
  LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('app.workspace_id', true), '')::ulid $$;

-- ---- 4. Enum types (Canon §5 / §6 / §13.3) ----------------------------------
-- Each guarded so re-runs don't fail (CREATE TYPE has no IF NOT EXISTS).
DO $$ BEGIN CREATE TYPE mission_stage    AS ENUM ('backlog','spec','building','qa','review','shipped'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE mission_source   AS ENUM ('jira','linear','github','manual','sentry','gitlab'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE mission_priority AS ENUM ('p0','p1','p2','p3'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;  -- stored lowercase; JSON exposes P0..P3 (§13.3)
DO $$ BEGIN CREATE TYPE autonomy_level   AS ENUM ('manual','assisted','supervised','autonomous'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE run_status       AS ENUM ('queued','running','paused','blocked','succeeded','failed','cancelled'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE blocker_kind     AS ENUM ('approval','question','limit','token','budget','error','dependency'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE blocker_severity AS ENUM ('warn','block'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE approval_gate    AS ENUM ('merge','deploy','spend','external','delete','account'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE agent_role_key   AS ENUM ('ceo','cto','pm','ba','backend','frontend','qa','devops','designer','security','custom'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE agent_status     AS ENUM ('working','review','idle','blocked'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE agent_level      AS ENUM ('junior','senior','principal','strategic'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE connection_status AS ENUM ('connected','disconnected','error','expired'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE model_provider   AS ENUM ('anthropic','openai','google','ollama','azure','custom'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE model_kind       AS ENUM ('cloud','local'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE model_tier       AS ENUM ('frontier','balanced','fast','local'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;  -- §13.3
DO $$ BEGIN CREATE TYPE skill_category   AS ENUM ('product','engineering','quality','security','devops','design','other'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;  -- §13.3
DO $$ BEGIN CREATE TYPE skill_source     AS ENUM ('built-in','custom','marketplace'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE memory_type      AS ENUM ('project','feedback','reference','user'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE member_role      AS ENUM ('owner','admin','operator','viewer'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE org_plan         AS ENUM ('free','team','business','enterprise'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE repo_provider    AS ENUM ('github','gitlab','bitbucket'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---- 5. Tables (created top-down in FK dependency order) ---------------------

-- 5.1 Tenancy root -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS orgs (
  id          ulid PRIMARY KEY DEFAULT gen_ulid(),
  name        text     NOT NULL,
  slug        citext   NOT NULL,
  plan        org_plan NOT NULL DEFAULT 'free',
  require_mfa boolean  NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  deleted_at  timestamptz,
  CONSTRAINT orgs_slug_uq UNIQUE (slug)
);

CREATE TABLE IF NOT EXISTS users (
  id          ulid PRIMARY KEY DEFAULT gen_ulid(),
  email       citext   NOT NULL,
  name        text     NOT NULL,
  avatar_url  text,
  idp_subject text,                         -- OIDC subject (Ory Kratos); doc 02 / 10-security
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  deleted_at  timestamptz,
  CONSTRAINT users_email_uq UNIQUE (email)
);

CREATE TABLE IF NOT EXISTS workspaces (
  id          ulid PRIMARY KEY DEFAULT gen_ulid(),
  org_id      ulid NOT NULL REFERENCES orgs(id) ON DELETE RESTRICT,
  name        text NOT NULL,
  slug        citext NOT NULL,              -- unique within org
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  deleted_at  timestamptz,
  CONSTRAINT workspaces_slug_uq UNIQUE (org_id, slug)
);

CREATE TABLE IF NOT EXISTS memberships (
  id           ulid PRIMARY KEY DEFAULT gen_ulid(),
  org_id       ulid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  workspace_id ulid REFERENCES workspaces(id) ON DELETE CASCADE,  -- NULL = org-wide
  user_id      ulid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role         member_role NOT NULL DEFAULT 'viewer',
  epoch        integer NOT NULL DEFAULT 0,  -- bump to invalidate this member's sessions/tokens
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz,
  CONSTRAINT memberships_uq UNIQUE (user_id, org_id, workspace_id)
);

-- 5.2 Repos ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS repos (
  id             ulid PRIMARY KEY DEFAULT gen_ulid(),
  org_id         ulid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  workspace_id   ulid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  provider       repo_provider NOT NULL,
  name           text NOT NULL,                -- e.g. 'web-app'
  url            text NOT NULL,
  default_branch text NOT NULL DEFAULT 'main',
  external_id    text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz,
  CONSTRAINT repos_ws_name_uq UNIQUE (workspace_id, name)
);

-- 5.3 Model connections ------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_connections (
  id             ulid PRIMARY KEY DEFAULT gen_ulid(),
  org_id         ulid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  workspace_id   ulid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  provider       model_provider NOT NULL,
  kind           model_kind NOT NULL,
  display_name   text NOT NULL,                -- 'Claude (Opus 4.8 · Sonnet 5 · Haiku 4.5)'
  endpoint       text,                         -- e.g. http://localhost:11434 for ollama
  credential_ref text,                         -- Vault path; NEVER the secret itself
  status         connection_status NOT NULL DEFAULT 'disconnected',
  is_primary     boolean NOT NULL DEFAULT false,
  config         jsonb NOT NULL DEFAULT '{}',
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz,
  CONSTRAINT model_conn_ws_provider_uq UNIQUE (workspace_id, provider)
);
-- Exactly one primary connection per workspace.
CREATE UNIQUE INDEX IF NOT EXISTS model_conn_one_primary
  ON model_connections (workspace_id)
  WHERE is_primary AND deleted_at IS NULL;

-- 5.4 Agents -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agents (
  id             ulid PRIMARY KEY DEFAULT gen_ulid(),
  org_id         ulid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  workspace_id   ulid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  role_key       agent_role_key NOT NULL,
  name           text NOT NULL,                -- 'Aria','Rhea'
  color          text,                         -- '#7c5cff' UI accent (from mockup)
  status         agent_status NOT NULL DEFAULT 'idle',
  level          agent_level NOT NULL DEFAULT 'senior',
  system_prompt  text,
  current_task   text,                         -- 'Implementing /api/v2/surcharge · FND-142'
  stats          jsonb NOT NULL DEFAULT '{"shipped":0,"prs":0,"reviews":0,"merged":0}',
  blurb          text,
  is_custom      boolean NOT NULL DEFAULT false,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz,
  CONSTRAINT agents_ws_name_uq UNIQUE (workspace_id, name)
  -- NOTE: agents.model_binding (FK → models) arrives with the `models` table in a
  -- later migration; the normalized model catalog is out of the Phase-0 subset.
);

-- 5.5 Skills -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skills (
  id           ulid PRIMARY KEY DEFAULT gen_ulid(),
  org_id       ulid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  workspace_id ulid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  slug         citext NOT NULL,               -- 'browser-qa','decision-ledger'
  name         text NOT NULL,
  description  text NOT NULL,
  category     skill_category NOT NULL DEFAULT 'other',
  source       skill_source NOT NULL DEFAULT 'built-in',
  trigger      text,                          -- when to auto-invoke
  instructions text,                          -- the skill body
  auto_invoke  boolean NOT NULL DEFAULT false,
  installed    boolean NOT NULL DEFAULT true,
  uses         integer NOT NULL DEFAULT 0,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz,
  CONSTRAINT skills_ws_slug_uq UNIQUE (workspace_id, slug)
);

-- 5.6 Missions ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS missions (
  id           ulid PRIMARY KEY DEFAULT gen_ulid(),
  org_id       ulid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  workspace_id ulid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  key          text NOT NULL,                 -- 'FND-142' (per-workspace counter; see 0002)
  title        text NOT NULL,
  summary      text,
  source       mission_source NOT NULL DEFAULT 'manual',
  ext_ref      text,                          -- 'NPD-11402','#3420','INB-233'
  priority     mission_priority NOT NULL DEFAULT 'p2',
  stage        mission_stage NOT NULL DEFAULT 'backlog',
  autonomy     autonomy_level NOT NULL DEFAULT 'supervised',
  progress     smallint NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  repo_id      ulid REFERENCES repos(id) ON DELETE SET NULL,
  branch       text,
  pr_url       text,                          -- denormalized convenience; pull_requests is SoT
  labels       text[] NOT NULL DEFAULT '{}',
  is_blocked   boolean NOT NULL DEFAULT false, -- derived; kept in sync by trg_blocker_sync
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz,
  CONSTRAINT missions_ws_key_uq UNIQUE (workspace_id, key)
);
COMMENT ON COLUMN missions.is_blocked IS
  'Derived flag maintained by the blocker insert/resolve/delete trigger; true when an unresolved blocker exists.';

-- 5.7 Blockers ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS blockers (
  id                  ulid PRIMARY KEY DEFAULT gen_ulid(),
  org_id              ulid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  workspace_id        ulid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  mission_id          ulid NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
  kind                blocker_kind NOT NULL,
  severity            blocker_severity NOT NULL,
  detail              text NOT NULL,
  model_connection_id ulid REFERENCES model_connections(id) ON DELETE SET NULL,  -- §13.11 (limit/token/budget)
  temporal_signal     text,                   -- signal name to resume the workflow
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  resolved_at         timestamptz,
  resolved_by         ulid REFERENCES users(id) ON DELETE SET NULL,
  resolution_note     text
);
-- At most one OPEN blocker of a given kind per mission (idempotent creation).
CREATE UNIQUE INDEX IF NOT EXISTS blockers_open_uq
  ON blockers (mission_id, kind)
  WHERE resolved_at IS NULL;

-- 5.8 Memories (pgvector) ----------------------------------------------------
CREATE TABLE IF NOT EXISTS memories (
  id              ulid PRIMARY KEY DEFAULT gen_ulid(),
  org_id          ulid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  workspace_id    ulid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  type            memory_type NOT NULL,
  title           text NOT NULL,
  body            text NOT NULL,
  embedding       vector(1536),               -- §13.10: text-embedding-3-small dim (RAG target)
  embedding_model text,                        -- provenance
  source_ref      text,                        -- commit/PR/mission the fact was pinned to
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  deleted_at      timestamptz
);

-- ---- 6. Indexes (doc 02 §5 — the Phase-0 hot paths) -------------------------
-- Missions board queries + attention panel + label filter.
CREATE INDEX IF NOT EXISTS missions_ws_stage_idx   ON missions (workspace_id, stage) WHERE deleted_at IS NULL;  -- required helper index
CREATE INDEX IF NOT EXISTS missions_ws_updated_idx ON missions (workspace_id, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS missions_blocked_idx    ON missions (workspace_id) WHERE is_blocked AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS missions_labels_gin     ON missions USING gin (labels);  -- required GIN index
CREATE INDEX IF NOT EXISTS missions_repo_idx       ON missions (repo_id) WHERE deleted_at IS NULL;

-- Agents / blockers.
CREATE INDEX IF NOT EXISTS agents_ws_status_idx    ON agents (workspace_id, status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS blockers_open_idx       ON blockers (workspace_id, kind, severity) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS blockers_mission_idx    ON blockers (mission_id, created_at DESC);

-- Skills / memory / models.
CREATE INDEX IF NOT EXISTS skills_ws_installed_idx ON skills (workspace_id) WHERE installed AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS memories_ws_type_idx    ON memories (workspace_id, type) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS memberships_user_idx    ON memberships (user_id);

-- pgvector HNSW (cosine) — RAG retrieval target (doc 02 §7).
CREATE INDEX IF NOT EXISTS memories_embedding_hnsw
  ON memories USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- ---- 7. Triggers ------------------------------------------------------------
-- 7.1 updated_at on every mutable table.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'orgs','users','workspaces','memberships','repos','model_connections',
    'agents','skills','missions','blockers','memories'
  ] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS trg_%1$s_updated_at ON %1$s;', t);
    EXECUTE format(
      'CREATE TRIGGER trg_%1$s_updated_at BEFORE UPDATE ON %1$s
         FOR EACH ROW EXECUTE FUNCTION set_updated_at();', t);
  END LOOP;
END $$;

-- 7.2 Blocker → mission.is_blocked sync (Canon §6; doc 02 §12).
CREATE OR REPLACE FUNCTION sync_mission_blocked() RETURNS trigger AS $$
BEGIN
  UPDATE missions m
     SET is_blocked = EXISTS (
       SELECT 1 FROM blockers b
       WHERE b.mission_id = m.id AND b.resolved_at IS NULL)
   WHERE m.id = COALESCE(NEW.mission_id, OLD.mission_id);
  RETURN COALESCE(NEW, OLD);
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_blocker_sync ON blockers;
CREATE TRIGGER trg_blocker_sync
  AFTER INSERT OR UPDATE OF resolved_at OR DELETE ON blockers
  FOR EACH ROW EXECUTE FUNCTION sync_mission_blocked();

-- ---- 8. Row-Level Security (Canon §1.6 / §13.2) -----------------------------
-- Cross-tenant access is impossible by construction: every scoped table filters
-- on the per-transaction tenant GUCs (app.workspace_id / app.org_id).
--
-- Phase-0 uses ENABLE (not FORCE) ROW LEVEL SECURITY so the object owner running
-- migrations/seeds via psql is not filtered. A later security migration adds a
-- non-owner `foundry_app` role + FORCE ROW LEVEL SECURITY (doc 02 §6.1).
-- orgs/users are the tenancy root and are not workspace-scoped; their RLS is
-- defined in the security migration (doc 02 §6.3), not here.

-- 8.1 Workspace-scoped tables.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'repos','model_connections','agents','skills','missions','blockers','memories'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I;', t || '_tenant_isolation', t);
    EXECUTE format(
      'CREATE POLICY %I ON %I
         USING (workspace_id = current_workspace_id())
         WITH CHECK (workspace_id = current_workspace_id()
                     AND org_id = current_org_id());',
      t || '_tenant_isolation', t);
  END LOOP;
END $$;

-- 8.2 Org-scoped tables.
ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workspaces_org_isolation ON workspaces;
CREATE POLICY workspaces_org_isolation ON workspaces
  USING (org_id = current_org_id())
  WITH CHECK (org_id = current_org_id());

ALTER TABLE memberships ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS memberships_org_isolation ON memberships;
CREATE POLICY memberships_org_isolation ON memberships
  USING (org_id = current_org_id())
  WITH CHECK (org_id = current_org_id());

-- =============================================================================
-- End 0001_init.sql  —  next: 0002_functions.sql (mission-key sequencing)
-- =============================================================================
