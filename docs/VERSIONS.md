# Shipwright — Pinned versions & ports (build spine)

**Authoritative for the build.** Every service/package uses exactly these versions and ports so
the monorepo is coherent and `docker compose up` "just works". This file pins the concrete
toolchain (the design left some versions as "latest").

## Languages & runtimes
| Tool | Version | Notes |
|---|---|---|
| Node.js | **24.x** (engines `>=22`) | verified present: v24 |
| pnpm | **11.x** (`packageManager` pinned) | monorepo package manager |
| Turborepo | **2.x** | task runner |
| TypeScript | **5.7.x** | |
| Next.js | **15.x** (App Router) | React 19 |
| React | **19.x** | |
| Tailwind CSS | **4.x** | `@tailwindcss/postcss` |
| Python | **3.13** | pinned via `uv` / `.python-version` |
| FastAPI | **>=0.115** | Pydantic v2 |
| Pydantic | **>=2.9** | + pydantic-settings >=2.5 |
| Temporal (Python SDK) | **temporalio >=1.8** | durable workflows |
| SQLAlchemy | **>=2.0** | async + asyncpg |
| Go | **1.24** | ingester (no compiler in this env — scaffold only) |
| PostgreSQL | **16** + **pgvector 0.7+** | primary DB + embeddings |
| Redis | **7.x** | streams / pub-sub / cache |
| ClickHouse | **24.x** | usage/event analytics |
| Temporal server | **1.25+** | + Temporal UI |
| MinIO | latest | S3-compatible object store (dev) |
| Vault | **1.x** | secrets (dev mode locally) |

## Ports (local dev — see `deploy/docker/docker-compose.yml` and `.env.example`)
| Service | Port |
|---|---|
| web (Next.js) | **3000** |
| orchestrator (FastAPI) | **8000** |
| ingester (Go) | **8090** |
| Postgres | **5432** |
| Redis | **6379** |
| Temporal (gRPC) | **7233** |
| Temporal UI | **8088** |
| ClickHouse (HTTP / native) | **8123 / 9000** |
| MinIO (API / console) | **9100 / 9101** |
| Vault | **8200** |
| Prometheus | **9090** |
| Grafana | **3001** |

## Temporal task queues (Canon §13.1)
`mission-workflow` · `agent-loop` · `integration-io` · `runner-dev` · `qa-browser`
Workflow id format: `run:{run_id}`.

## Conventions
- JSON is camelCase; DB is snake_case (Canon §8). IDs are ULIDs; mission keys `FND-<n>`.
- Tenant GUCs: `app.workspace_id` / `app.org_id` (Canon §13.2).
- Error envelope: `{ "error": { code, message, details, requestId, retryable } }` (Canon §13.5).
