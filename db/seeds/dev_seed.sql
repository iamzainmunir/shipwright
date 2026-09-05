-- =============================================================================
-- Shipwright — development seed (a self-contained slice for local dev)
-- =============================================================================
-- Development seed dataset. This is the
-- Phase-0 slice: 1 org, 1 workspace, 1 owner user + membership, 1 repo,
-- 2 model connections (anthropic cloud + ollama local), 4 agents, 2 skills,
-- 2 memories, 4 missions — two of them BLOCKED (a `question` on FND-128 and a
-- `limit` on FND-138, matching the dashboard's attention panel).
--
-- Depends on: 0001_init.sql + 0002_functions.sql.
-- Reproducible: every row uses a FIXED ULID-like id (26-char Crockford base32,
-- no I/L/O/U), so re-running yields identical ids. Idempotent via ON CONFLICT
-- DO NOTHING (safe to re-run against an already-seeded database).
--
-- Fixed-id scheme:  01JBWFND0000000000000000<TAG>
--   G0 org · W0 workspace · Q0 user · M0 membership · R0 repo
--   C0 anthropic conn · C1 ollama conn
--   A0 pm · A1 backend · A2 qa · A3 cto
--   N0 FND-142 · N1 FND-128 · N2 FND-138 · N3 FND-140
--   B0 question blocker · B1 limit blocker
--   S0 test-driven-dev · S1 browser-qa · Y0/Y1 memories
-- =============================================================================

BEGIN;

-- Set tenant context so the seed also succeeds under FORCE ROW LEVEL SECURITY
-- (Phase-0 RLS is ENABLE-only, where the owner bypasses; this is belt-and-braces
-- and demonstrates the app's per-request pattern — Canon §13.2).
SET LOCAL app.org_id       = '01JBWFND0000000000000000G0';
SET LOCAL app.workspace_id = '01JBWFND0000000000000000W0';

-- ---- Org + workspace + owner ------------------------------------------------
INSERT INTO orgs (id, name, slug, plan) VALUES
  ('01JBWFND0000000000000000G0', 'Acme Inc', 'acme', 'business')
ON CONFLICT DO NOTHING;

INSERT INTO workspaces (id, org_id, name, slug) VALUES
  ('01JBWFND0000000000000000W0', '01JBWFND0000000000000000G0', 'NPD Platform', 'npd')
ON CONFLICT DO NOTHING;

INSERT INTO users (id, email, name) VALUES
  ('01JBWFND0000000000000000Q0', 'owner@acme.example', 'Owner')
ON CONFLICT DO NOTHING;

INSERT INTO memberships (id, org_id, workspace_id, user_id, role) VALUES
  ('01JBWFND0000000000000000M0', '01JBWFND0000000000000000G0',
   '01JBWFND0000000000000000W0', '01JBWFND0000000000000000Q0', 'owner')
ON CONFLICT DO NOTHING;

-- Seed the FND-<n> counter so the next generated key is FND-147 (matches doc 02).
INSERT INTO mission_key_counters (workspace_id, next_value) VALUES
  ('01JBWFND0000000000000000W0', 147)
ON CONFLICT DO NOTHING;

-- ---- Repo -------------------------------------------------------------------
INSERT INTO repos (id, org_id, workspace_id, provider, name, url, default_branch) VALUES
  ('01JBWFND0000000000000000R0', '01JBWFND0000000000000000G0', '01JBWFND0000000000000000W0',
   'github', 'web-app', 'https://github.com/acme/web-app', 'main')
ON CONFLICT DO NOTHING;

-- ---- Model connections (anthropic cloud primary + ollama local) -------------
INSERT INTO model_connections
  (id, org_id, workspace_id, provider, kind, display_name, endpoint, status, is_primary) VALUES
  ('01JBWFND0000000000000000C0', '01JBWFND0000000000000000G0', '01JBWFND0000000000000000W0',
   'anthropic', 'cloud', 'Claude (Opus 4.8 · Sonnet 5 · Haiku 4.5)', NULL, 'connected', true),
  ('01JBWFND0000000000000000C1', '01JBWFND0000000000000000G0', '01JBWFND0000000000000000W0',
   'ollama', 'local', 'llama3.1:70b · qwen2.5-coder:32b · deepseek-r1',
   'http://localhost:11434', 'connected', false)
ON CONFLICT DO NOTHING;

-- ---- Agents (4) -------------------------------------------------------------
INSERT INTO agents
  (id, org_id, workspace_id, role_key, name, color, status, level, current_task, stats, blurb) VALUES
  ('01JBWFND0000000000000000A0', '01JBWFND0000000000000000G0', '01JBWFND0000000000000000W0',
   'pm', 'Noor', '#34d3ee', 'working', 'senior', 'Writing acceptance criteria · FND-128',
   '{"shipped":167,"prs":4,"reviews":203,"merged":0}',
   'Turns intent into specs & stories. Runs the intake interview.'),
  ('01JBWFND0000000000000000A1', '01JBWFND0000000000000000G0', '01JBWFND0000000000000000W0',
   'backend', 'Rhea', '#3ad29f', 'working', 'senior', 'Implementing /api/v2/surcharge · FND-142',
   '{"shipped":398,"prs":76,"reviews":120,"merged":71}',
   'Builds services, models, and jobs. Test-first by default.'),
  ('01JBWFND0000000000000000A2', '01JBWFND0000000000000000G0', '01JBWFND0000000000000000W0',
   'qa', 'Mira', '#f6c454', 'working', 'senior', 'Running Gherkin suite in Chrome · FND-140',
   '{"shipped":0,"prs":9,"reviews":512,"merged":0}',
   'Drives a real browser, runs stories, files defects with severity.'),
  ('01JBWFND0000000000000000A3', '01JBWFND0000000000000000G0', '01JBWFND0000000000000000W0',
   'cto', 'Kade', '#5b8cff', 'review', 'principal', 'Architecture review · FND-142',
   '{"shipped":189,"prs":12,"reviews":412,"merged":0}',
   'Owns architecture, tech bar, and risk. Final review gate.')
ON CONFLICT DO NOTHING;

-- ---- Skills (2) -------------------------------------------------------------
INSERT INTO skills
  (id, org_id, workspace_id, slug, name, description, category, source, auto_invoke, installed, uses) VALUES
  ('01JBWFND0000000000000000S0', '01JBWFND0000000000000000G0', '01JBWFND0000000000000000W0',
   'test-driven-dev', 'test-driven-dev',
   'Write a failing test first, then the minimum code to pass it.',
   'engineering', 'built-in', true, true, 876),
  ('01JBWFND0000000000000000S1', '01JBWFND0000000000000000G0', '01JBWFND0000000000000000W0',
   'browser-qa', 'browser-qa',
   'Drive a real Chrome session to run acceptance stories end-to-end.',
   'quality', 'built-in', false, true, 288)
ON CONFLICT DO NOTHING;

-- ---- Memories (2) — embeddings backfilled by an async job post-seed ---------
INSERT INTO memories (id, org_id, workspace_id, type, title, body) VALUES
  ('01JBWFND0000000000000000Y0', '01JBWFND0000000000000000G0', '01JBWFND0000000000000000W0',
   'project', 'Code is the source of truth',
   'Every behavioural claim is verified against code and pinned to the commit it was verified at. Never present unshipped behaviour as reality.'),
  ('01JBWFND0000000000000000Y1', '01JBWFND0000000000000000G0', '01JBWFND0000000000000000W0',
   'project', 'Tenant scoping is non-negotiable',
   'Every by-id read/write/delete must pin school from the JWT. Cross-tenant paths block merge automatically.')
ON CONFLICT DO NOTHING;

-- ---- Missions (4) — two of them will become blocked below -------------------
INSERT INTO missions
  (id, org_id, workspace_id, key, title, summary, source, ext_ref, priority, stage, autonomy,
   progress, repo_id, branch, pr_url, labels) VALUES
  ('01JBWFND0000000000000000N0', '01JBWFND0000000000000000G0', '01JBWFND0000000000000000W0',
   'FND-142', 'Late-surcharge setup: fix cross-tenant by-id read/write',
   'By-id read/update/delete on late-surcharge is not tenant-scoped (D1, critical). Add school scoping + RBAC + regression stories.',
   'jira', 'NPD-11402', 'p0', 'building', 'supervised', 64,
   '01JBWFND0000000000000000R0', 'fix/NPD-11402-surcharge-scoping', '#3441',
   '{security,billing,backend}'),
  ('01JBWFND0000000000000000N1', '01JBWFND0000000000000000G0', '01JBWFND0000000000000000W0',
   'FND-128', 'Bill reminder: per-branch delivery rules + SMS fallback',
   'Owner wants per-branch reminder rules. Intake interview running; ledger capturing the access matrix and limits.',
   'jira', 'NPD-11377', 'p1', 'spec', 'assisted', 22,
   '01JBWFND0000000000000000R0', NULL, NULL, '{billing,notifications}'),
  ('01JBWFND0000000000000000N2', '01JBWFND0000000000000000G0', '01JBWFND0000000000000000W0',
   'FND-138', 'Event RSVP capacity counted two different ways',
   'Web path counts declines as seats and blocks changing an answer; mobile is correct. Unify the count. Paused when the Anthropic daily token cap was hit.',
   'linear', 'EVT-88', 'p1', 'building', 'supervised', 41,
   '01JBWFND0000000000000000R0', 'fix/EVT-88-rsvp-capacity', '#3435', '{events,bug}'),
  ('01JBWFND0000000000000000N3', '01JBWFND0000000000000000G0', '01JBWFND0000000000000000W0',
   'FND-140', 'Inbox: reopen guard must refuse Expired & Archived threads',
   'reopenThread has no status precondition (D13). QA running 14 Gherkin scenarios against a real browser.',
   'linear', 'INB-233', 'p0', 'qa', 'supervised', 88,
   '01JBWFND0000000000000000R0', 'fix/INB-233-reopen-guard', '#3438',
   '{inbox,security,regression}')
ON CONFLICT DO NOTHING;

-- ---- Mission assignees ------------------------------------------------------
-- (kept as direct links; the mission_assignees join table is added with the
--  full schema in a later migration — Phase-0 seed wires blockers/steps only.)

-- ---- The 2 seeded blockers (trigger flips missions.is_blocked → true) -------
-- FND-128 → question (warn, no connection) · FND-138 → limit (block, anthropic).
INSERT INTO blockers
  (id, org_id, workspace_id, mission_id, kind, severity, detail, model_connection_id) VALUES
  ('01JBWFND0000000000000000B0', '01JBWFND0000000000000000G0', '01JBWFND0000000000000000W0',
   '01JBWFND0000000000000000N1', 'question', 'warn',
   'Noor needs a decision: should reminder rules be per-branch or per-grade? The spec can''t lock without it.',
   NULL),
  ('01JBWFND0000000000000000B1', '01JBWFND0000000000000000G0', '01JBWFND0000000000000000W0',
   '01JBWFND0000000000000000N2', 'limit', 'block',
   'Anthropic daily token cap reached — Rhea is paused. Raise the cap or route this mission to a local model.',
   '01JBWFND0000000000000000C0')
ON CONFLICT DO NOTHING;

COMMIT;

-- ---- Seed verification (should all pass) ------------------------------------
--   SELECT count(*) FROM agents;                                  -- 4
--   SELECT count(*) FROM missions;                                -- 4
--   SELECT count(*) FROM model_connections;                       -- 2
--   SELECT count(*) FROM skills;                                  -- 2
--   SELECT count(*) FROM memories;                                -- 2
--   SELECT count(*) FROM blockers WHERE resolved_at IS NULL;      -- 2
--   SELECT key, is_blocked FROM missions ORDER BY key;
--     -- FND-128 → t, FND-138 → t, FND-140 → f, FND-142 → f
--   SELECT next_mission_key('01JBWFND0000000000000000W0');        -- 'FND-147'
-- =============================================================================
