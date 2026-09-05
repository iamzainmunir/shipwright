# deploy — local infra & container builds

Everything needed to run the Shipwright backbone locally and to build the app service images.
Versions, images, and ports are pinned in [`../docs/VERSIONS.md`](../docs/VERSIONS.md);
environment variable names match [`../.env.example`](../.env.example).

```
deploy/
  docker/
    docker-compose.yml       # local backbone: postgres, redis, temporal(+ui), clickhouse, minio, vault
    web.Dockerfile           # apps/web (Next.js 15)
    orchestrator.Dockerfile  # services/orchestrator (FastAPI, uv)
    ingester.Dockerfile      # services/ingester (Go 1.24)
  helm/
    README.md                # Kubernetes charts — Phase 4 placeholder
```

## Prerequisites

- Docker (with Compose v2) running.
- `cp ../.env.example ../.env` at the repo root (optional — the compose file ships dev defaults for
  every variable, so it also runs unchanged). To make your edited root `.env` take effect for
  Compose interpolation, run with `--env-file ../../.env` or `export` the vars first.
- The `db` subtree must have produced its SQL so the DB can self-initialize (see **Auto-seed** below):
  - `../db/migrations/0001_init.sql`
  - `../db/migrations/0002_functions.sql`
  - `../db/seeds/dev_seed.sql`

## Bring the backbone up / down

From the **repo root** (wrapper scripts already defined in the root `package.json`):

```bash
pnpm infra:up      # docker compose -f deploy/docker/docker-compose.yml up -d
pnpm infra:down    # docker compose -f deploy/docker/docker-compose.yml down
```

Or directly:

```bash
docker compose -f deploy/docker/docker-compose.yml up -d
docker compose -f deploy/docker/docker-compose.yml ps          # health status
docker compose -f deploy/docker/docker-compose.yml logs -f temporal
docker compose -f deploy/docker/docker-compose.yml down         # stop, keep data
docker compose -f deploy/docker/docker-compose.yml down -v      # stop AND wipe volumes (forces a fresh re-init + re-seed)
```

## Ports (local dev — from `../docs/VERSIONS.md`)

| Service | Host port(s) | Notes |
|---|---|---|
| Postgres (+ pgvector) | `5432` | user/db/password from `.env` (`foundry` / `foundry` / `foundry_dev`) |
| Redis | `6379` | AOF persistence on |
| Temporal (gRPC) | `7233` | schema + `default` namespace auto-created in Postgres |
| Temporal UI | `8088` | http://localhost:8088 |
| ClickHouse | `8123` / `9000` | HTTP / native |
| MinIO API / Console | `9100` / `9101` | console http://localhost:9101 |
| Vault (dev) | `8200` | root token `foundry-dev-root` (`VAULT_TOKEN`) |

App services (built from the Dockerfiles below, not part of this compose): web `3000`,
orchestrator `8000`, ingester `8090`.

## How the database is auto-seeded

Postgres runs any files mounted into `/docker-entrypoint-initdb.d` **once**, on first boot of an
empty data volume, in filename order. The compose file mounts:

1. `0001_init.sql`  → schema (from `../db/migrations/`)
2. `0002_functions.sql` → functions/RLS helpers (from `../db/migrations/`)
3. `0003_dev_seed.sql` → dev data (mounted from `../db/seeds/dev_seed.sql`)

So a brand-new database comes up already migrated and seeded. These scripts **do not** re-run on
subsequent starts. To reset and re-seed, drop the volume: `docker compose ... down -v` then `up`.

MinIO's `foundry-artifacts` bucket (`S3_BUCKET`) is created by the one-shot `createbuckets`
service after MinIO is healthy.

## Build the service images

Build contexts are the **repo root** (the web and orchestrator images depend on workspace
siblings — JS `workspace:*` packages / the editable `foundry-core` lib):

```bash
docker build -f deploy/docker/web.Dockerfile          -t foundry/web          .
docker build -f deploy/docker/orchestrator.Dockerfile -t foundry/orchestrator .
docker build -f deploy/docker/ingester.Dockerfile     -t foundry/ingester     .
```

- **web.Dockerfile** — `node:24-alpine`, pnpm via corepack, installs the workspace, builds
  `@foundry/web`, runs `next start` on `:3000`.
- **orchestrator.Dockerfile** — `python:3.13-slim` + `uv`, `uv sync` (pulls the editable
  `foundry-core` from `libs/py-core`), serves Uvicorn on `:8000`. The same image runs the
  Temporal workflow/activity workers with a different command.
- **ingester.Dockerfile** — multi-stage `golang:1.24` build → static binary on `alpine`, `:8090`.

## Kubernetes

Helm charts are a Phase 4 deliverable — see [`helm/README.md`](helm/README.md).
