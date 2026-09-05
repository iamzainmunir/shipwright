# `db/` — Shipwright database schema & seed

SQL migrations and the development seed for Shipwright's primary store:
**PostgreSQL 16 + pgvector**. This subtree is Phase-0 scaffolding — a real,
runnable **core subset** of the full data layer. The complete, DDL-level design
(every entity, index, RLS policy, plus the Redis / ClickHouse / S3 layers) is
added incrementally across later phases; pinned versions and ports are in
`docs/VERSIONS.md`.

## Layout

```
db/
  migrations/
    0001_init.sql        # extensions, ulid domain, enums, 11 core tables, indexes,
                         # triggers, Row-Level Security policies
    0002_functions.sql   # mission_key_counters + next_mission_key() (FND-<n>)
    README.md            # how to apply (psql + docker-compose auto-load)
  seeds/
    dev_seed.sql         # 1 org / workspace / owner, 2 model connections, 4 agents,
                         # 2 skills, 2 memories, 4 missions (2 blocked)
  README.md              # this file
```

## What Phase-0 includes

- **Tables:** `orgs`, `users`, `workspaces`, `memberships`, `repos`,
  `model_connections`, `agents`, `skills`, `missions`, `blockers`, `memories`,
  plus `mission_key_counters`.
- **Conventions (Canon §8 / §13):** ULID ids (`ulid` domain over `char(26)`),
  snake_case columns, `timestamptz` `_at` audit columns, JSON stored as `jsonb`.
- **Tenancy:** every workspace-scoped table has RLS keyed on the per-request GUCs
  `app.workspace_id` / `app.org_id` (Canon §13.2); an unset or foreign context
  matches zero rows.
- **Derived state:** a trigger keeps `missions.is_blocked` in sync with open
  `blockers` rows (drives the board's attention panel).
- **Vector memory:** `memories.embedding vector(1536)` with an HNSW cosine index
  (embedding model `text-embedding-3-small`, Canon §13.10).
- **Mission keys:** `next_mission_key(workspace)` allocates `FND-<n>` per
  workspace from a durable counter.

## Run it

See `migrations/README.md` for the full recipe. Quickest path:

```bash
export PGHOST=localhost PGPORT=5432 PGUSER=foundry PGPASSWORD=foundry_dev PGDATABASE=foundry
psql -v ON_ERROR_STOP=1 -f db/migrations/0001_init.sql
psql -v ON_ERROR_STOP=1 -f db/migrations/0002_functions.sql
psql -v ON_ERROR_STOP=1 -f db/seeds/dev_seed.sql
```

Under Docker Compose the Postgres service (`pgvector/pgvector:pg16`) auto-loads
`migrations/` then `seeds/` on first init, so `docker compose up` yields a
migrated + seeded database.

## Verify the seed

```sql
SELECT count(*) FROM agents;                     -- 4
SELECT count(*) FROM missions;                   -- 4
SELECT count(*) FROM blockers WHERE resolved_at IS NULL;  -- 2 (question + limit)
SELECT key, is_blocked FROM missions ORDER BY key;        -- FND-128,FND-138 = true
SELECT next_mission_key('01JBWFND0000000000000000W0');    -- 'FND-147'
```

Migrations, `next_mission_key`, the blocker trigger, both partial-unique
constraints, and RLS tenant isolation were validated against a live
PostgreSQL 16 instance.
