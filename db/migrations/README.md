# Shipwright — database migrations

Raw-SQL migrations for the Shipwright PostgreSQL 16 (+ pgvector) database. This is
the **Phase-0 core subset**; the complete DDL-level design is added
incrementally. Later phases add the remaining
tables (runs, steps, events, approvals, models, usage rollups, defects,
sandboxes, deployments, billing, auth) and move
migration ownership under Alembic (`db/migrations/postgres/`).

## Files (apply in lexical order)

| File | What it does |
|------|--------------|
| `0001_init.sql` | Extensions (`pgcrypto`, `vector`, `citext`), the `ulid` domain + `gen_ulid()`, tenant-context helpers, all Phase-0 enum types, the 11 core tables (orgs, users, workspaces, memberships, repos, model_connections, agents, skills, missions, blockers, memories), indexes (incl. the `missions(workspace_id, stage)` helper index, the GIN index on `missions.labels`, and the HNSW vector index), the `updated_at` and blocker→`missions.is_blocked` triggers, and Row-Level Security policies keyed on the `app.workspace_id` / `app.org_id` GUCs (Canon §13.2). |
| `0002_functions.sql` | `mission_key_counters` table + `next_mission_key(workspace)` — the durable, per-workspace `FND-<n>` allocator (Canon §8). |

Each file is idempotent-ish (guarded `CREATE TYPE`, `CREATE TABLE IF NOT EXISTS`,
`CREATE OR REPLACE`, `DROP … IF EXISTS` before `CREATE POLICY`/`CREATE TRIGGER`),
so re-applying against an existing database is safe.

## Requirements

- **PostgreSQL 16** with the **pgvector 0.7+** extension available
  (`CREATE EXTENSION vector`). `pgcrypto` and `citext` ship with the standard
  contrib set. The dev container image is `pgvector/pgvector:pg16`.

## Apply manually with `psql`

```bash
# uses the DATABASE_URL / POSTGRES_* from the repo root .env.example (db: foundry)
export PGHOST=localhost PGPORT=5432 PGUSER=foundry PGPASSWORD=foundry_dev PGDATABASE=foundry

psql -v ON_ERROR_STOP=1 -f db/migrations/0001_init.sql
psql -v ON_ERROR_STOP=1 -f db/migrations/0002_functions.sql
psql -v ON_ERROR_STOP=1 -f db/seeds/dev_seed.sql        # optional dev data
```

## Auto-load under Docker Compose

The Postgres service runs everything in `/docker-entrypoint-initdb.d/*.sql`
**once, in lexical order, on first cluster init** (empty data volume). The
compose file (`deploy/docker/docker-compose.yml`, owned by the `deploy` subtree)
mounts this directory and the seeds so a fresh `docker compose up` gives a
migrated + seeded database with no extra step:

```yaml
# deploy/docker/docker-compose.yml (reference — not owned here)
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: foundry
      POSTGRES_USER: foundry
      POSTGRES_PASSWORD: foundry_dev
    volumes:
      - ../../db/migrations:/docker-entrypoint-initdb.d/10-migrations:ro
      - ../../db/seeds:/docker-entrypoint-initdb.d/20-seeds:ro
```

The `10-` / `20-` prefixes guarantee migrations run before seeds. Because
init-scripts only run on an empty volume, re-running against an existing volume
is a no-op — apply new migrations with `psql` (above) or the Phase-1 Alembic
runner.

## Notes

- **RLS in Phase-0** uses `ENABLE ROW LEVEL SECURITY` (not `FORCE`) so the object
  owner running these scripts / the seed is not filtered. The Phase-1 security
  migration adds a non-owner `foundry_app` role and `FORCE ROW LEVEL SECURITY`
  (doc 02 §6.1) so the runtime role can never bypass tenant isolation.
- The app sets tenant context per transaction with
  `SET LOCAL app.workspace_id = '<ulid>'` and `SET LOCAL app.org_id = '<ulid>'`;
  every policy reads exactly those two settings via `current_workspace_id()` /
  `current_org_id()`.
