# services/orchestrator — Shipwright Orchestrator

The Python **3.13 / FastAPI** service that is the platform's brain: the public REST v1 API
(the platform's external contract) and, in later phases, the Temporal agent engine.
Dependencies are managed by **uv**; versions are pinned per [`../../docs/VERSIONS.md`](../../docs/VERSIONS.md).

Listens on **:8000** (Canon / VERSIONS.md).

## What's here (Phase 0)

```
app/
  config.py          Settings (pydantic-settings) — DATABASE_URL, REDIS_URL, TEMPORAL_HOST, CORS, env, log level
  errors.py          ApiError + handlers → the Canon §13.5 envelope {error:{code,message,details,requestId,retryable}}
  main.py            create_app(): CORS, structlog, X-Request-Id middleware, routers; `app = create_app()`
  api/health.py      GET /healthz, GET /readyz
  api/v1/            APIRouter(prefix="/api/v1")
    missions.py      GET/POST /api/v1/missions (camelCase JSON via Pydantic aliases)
tests/test_health.py TestClient: /healthz == 200 + the error envelope on a forced error
Dockerfile           python:3.13-slim + uv, runs uvicorn on 8000
```

`app/api/v1/missions.py` uses the shared `Mission` model from **`foundry-core`**
(`foundry_core.models`), wired via `[tool.uv.sources]` as an editable path dependency on
`../../libs/py-core`. A local fallback with the same camelCase shape keeps the service
booting and testable while `libs/py-core` is scaffolded in parallel.

## Run

```bash
cd services/orchestrator

uv sync                                       # resolve + install (incl. editable foundry-core)
uv run uvicorn app.main:app --port 8000       # http://localhost:8000
#   http://localhost:8000/healthz             → {"status":"ok","service":"orchestrator"}
#   http://localhost:8000/api/v1/missions     → [ {mission…} ]
#   http://localhost:8000/docs                → OpenAPI UI

uv run uvicorn app.main:app --reload --port 8000   # dev hot-reload
```

## Phase 1 — the run engine (in-process)

The agent run engine takes a mission through `intake → spec → build.api → qa → review → ship`,
calls an LLM provider per phase, and **suspends at the supervised merge gate** (an approval
blocker) until a human resolves it, then ships.

```
app/
  store.py           InMemoryStore (default) — the run aggregates
  seed.py            shared fixtures (agents, missions, connections, skills, memories)
  providers/         LLM abstraction: mock (offline default) + anthropic (ANTHROPIC_API_KEY)
  engine.py          RunEngine — phases, metering, gate = asyncio future (suspend/resume)
  events.py          in-process SSE event bus
  api/v1/runs.py     run / steps / events / blockers / approvals / metrics / events/stream (SSE)
```

```bash
# start a run, watch it suspend at the gate, approve, ship
curl -X POST localhost:8000/api/v1/missions/FND-142/run
curl        localhost:8000/api/v1/blockers
curl -X POST localhost:8000/api/v1/missions/FND-142/approvals -d '{"gate":"merge","decision":"approve"}' -H 'content-type: application/json'
```

## Phase 2 — Postgres persistence + Temporal worker

**Postgres store** (run state survives a restart). Point `SHIPWRIGHT_STORE=postgres` at a database:

```bash
createdb shipwright_dev   # local dev (pgvector not required for the run aggregates)
SHIPWRIGHT_STORE=postgres \
DATABASE_URL="postgresql+asyncpg://<user>@127.0.0.1:5432/shipwright_dev" \
  uv run uvicorn app.main:app --port 8000
# tables are auto-created + seeded on first boot (app/db_models.py, app/pgstore.py)
```

**Durable Temporal engine** (`app/engine_temporal.py`) — the merge gate is a Temporal signal,
so runs survive worker restarts. Run the worker on the `mission-workflow` queue:

```bash
uv run python -m app.engine_temporal        # needs a Temporal server (localhost:7233) + a shared store
```

The durable path is exercised end-to-end in `tests/test_temporal_worker.py` (spins up the SDK's
local dev server; skips if unavailable).

## Test / lint / types

```bash
uv run pytest        # tests/test_health.py
uv run ruff check .
uv run mypy app
```

## Docker

Build from the **repo root** (so the editable `foundry-core` path resolves):

```bash
docker build -f services/orchestrator/Dockerfile -t shipwright-orchestrator .
docker run -p 8000:8000 --env-file .env shipwright-orchestrator
```

## Configuration

`app/config.py` reads the environment from [`../../.env.example`](../../.env.example):
`DATABASE_URL`, `REDIS_URL`, `TEMPORAL_HOST`, `TEMPORAL_NAMESPACE`, `SHIPWRIGHT_ENV`,
`LOG_LEVEL`, `ORCHESTRATOR_PORT`, `CORS_ORIGINS`. Defaults target local dev, so the service
boots with no `.env` present.

## Conventions (Canon §13)

- JSON is **camelCase**; ids are **ULIDs**; error bodies use the §13.5 envelope.
- Every response carries **`X-Request-Id`** (== `error.requestId`) for tracing/support.
- Endpoints are tagged with their `x-required-scope` (Canon §13.6); enforcement lands with
  auth in a later phase.
