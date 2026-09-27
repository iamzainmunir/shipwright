<p align="center">
  <img src="logo.png" width="120" alt="Shipwright" />
</p>

<h1 align="center">Shipwright</h1>

**An autonomous AI software company.** You file a ticket; a team of AI agents — PM, architect, engineers, a researcher, QA, reviewer, DevOps — takes it through research, spec, planning, build, code review, QA, and ship, and hands you a running application. You hold the gates that matter. It **verifies before it ships, learns from every run, and can be driven end-to-end from WhatsApp.**

> New here? Read **[docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md)** for the full picture — the pipeline, the roles, the models, and the autonomy model, with the *what / how / when* of each.

![How Shipwright works](docs/images/how-it-works.svg)

![Command center dashboard](docs/images/dashboard.png)

---

## Screens

|  |  |
|---|---|
| **Live Build** — the pipeline running in real time, with one-click *Run app* on a shipped build.<br>![Live Build](docs/images/live-build.png) | **Ticket board** — a Jira-like board the team drives itself, with the QA evidence behind every verdict.<br>![Ticket board](docs/images/tickets.png) |
| **Models** — bind any provider per agent (Anthropic, OpenAI-compatible, Ollama, or Claude Code CLI), with failover and live metering.<br>![Models](docs/images/models.png) | **Team** — ten role-locked specialists laid out by the agentic SDLC, each with its own model binding.<br>![Team](docs/images/team.png) |
| **Projects** — pick one or more codebases and prompt one change the team coordinates across them (add an API in a service, wire it into the gateway).<br>![Projects](docs/images/projects.png) | **Missions** — the kanban board of work, from backlog through building, review, QA, and shipped.<br>![Missions](docs/images/missions.png) |

<sub>More in [`docs/images/`](docs/images). The landing page (`/`) has an auto-playing tour of these screens.</sub>

---

## What makes it different

Most AI coding tools are a **single agent in your editor** that writes code you then review, run, and verify
yourself. Shipwright is an **autonomous company** that owns the whole SDLC and is built around the parts that
make autonomy trustworthy:

- **It verifies before it ships — by construction.** A deterministic ground-truth gate overrides any
  hallucinated "looks good": an autonomous run auto-merges **only when the work was actually verified** (a
  real build + runtime/API evidence), otherwise it halts for you. A red test suite blocks the ship. It
  never auto-ships on a model's say-so.
- **It checks the APIs, not just the UI.** A single versioned **Spec Contract** binds spec → build → QA to
  the same expectations, and QA issues **real HTTP requests** asserting status, response shape, and error
  hygiene — a blocking API failure reopens the build.
- **It compounds.** Every rework, failure, and ship becomes a durable **lesson/skill** that later missions
  recall where code is actually written; skills that don't help are **auto-retired**. The org gets better
  the more it runs — and you can **see** exactly what knowledge is in play, live.
- **You run it from anywhere.** Full two-way control over **WhatsApp, Slack, and email** — start a mission,
  answer the AI's questions, and approve gates from a chat, with no app open.
- **It's a team, in parallel, across repos.** Role-locked specialists (incl. an internet **Researcher**)
  build in parallel git worktrees and can coordinate one change **across several codebases** under one gate.
- **Bring your own models, including your Claude seat.** Any provider per agent, or drive the local
  `claude` CLI (your subscription) as a provider.

## What it does

Give Shipwright a brief ("Build a simple notes app", "Fix this bug", "Add an endpoint"). It:

1. **Frames the work** — a PM reads the ticket, asks clarifying questions, and drafts a spec with machine-checkable acceptance criteria.
2. **Plans** — the PM + CTO break the spec into a dependency-ordered task graph and create tickets on a built-in board, one per part, assigned to the right engineer by role and skill.
3. **Builds** — engineers implement their slices **in parallel**, each in an isolated git worktree, then the work is merged into one coherent codebase.
4. **Reviews the code** — the CTO reviews correctness, security, scope, and the tech bar.
5. **Verifies it works** — QA runs the *real* app (boots its server, drives a headless browser, **and issues real API requests** against the Spec Contract) and checks each acceptance criterion by behaviour.
6. **Ships** — DevOps merges/deploys behind the approval gate you configured — **and only when the build is actually verified**.

Every deliverable is real, runnable code on disk — never a mock. When the automated team can't produce something shippable, it **stops and asks you** rather than shipping broken work. And every run feeds the org's **memory + skills**, so the next mission starts smarter.

## How it works (the short version)

The run is a **decision graph**, not a straight line — a phase's verdict can send work forward, back to an earlier phase, or escalate to the CTO:

```
intake → clarify → spec → plan → build → review → QA → ship
                                   ▲        │       │
                                   └── rework ◄─────┘   (QA/review send fixes back to the failing part only)
                                        │
                                   CTO decision (redesign → spec · rebuild → build · proceed → QA/ship)
```

- **Code review comes before QA:** the CTO reviews the code first, then QA verifies the reviewed build's behaviour as the final gate before ship — so what ships is exactly what QA blessed.
- **Targeted rework:** when QA or review flags a problem, only the failing part rebuilds (off the current code) — the rest is untouched.
- **Every loop is bounded:** rework budgets and a CTO-consultation cap keep any cycle finite; when they're exhausted, the run halts for you.
- **Never ships unverified:** a deterministic gate over-rides any hallucinated "looks good" — QA must actually pass and the build must be real.

### The team

Each mission runs on a **team** of role-locked agents — every agent does only its own job:

| Role | Does | Does NOT |
|---|---|---|
| **PM** | scope, spec, acceptance criteria, plan | write code or design visuals |
| **CTO** | architecture decisions + the code review | write day-to-day feature code |
| **Backend** | server APIs, data, business logic | UI/styling |
| **Frontend** | UI, components, client state | server APIs / data models |
| **QA** | verify behaviour against acceptance criteria **+ the API contract** | review code style/architecture |
| **DevOps** | merge, deploy, rollback | write product features |
| **Researcher** | search + read the internet (read-only), produce **cited** briefs | write code, design, or decide |
| **Designer / BA / Security** | UX, requirements, threat modelling | out-of-lane work |

If a builder is genuinely blocked or a requirement conflicts, it **asks the CTO** instead of guessing, and continues with the decision.

### Models

Assign any model to any agent — mix and match per role:

- **Anthropic API**, **OpenAI / Gemini / Groq / Mistral / DeepSeek / …** (OpenAI-compatible), **local Ollama** (free), or any OpenAI-compatible endpoint.
- **Claude Code CLI** — drive your local `claude` binary (your Claude subscription seat) as a provider: `auto`/`opus`/`sonnet`/`haiku`, an effort level, and a build-autonomy setting. Reasoning phases run `claude -p`; the build runs the CLI as a coding agent in each worktree. (It subprocesses the official binary — the sanctioned way to use a seat programmatically.)

Models per agent, failover chains, budgets and rate caps are all configured under **Models**.

### Autonomy — *when* you're in the loop

Set per mission: **manual → assisted → supervised → autonomous**. Autonomous runs the whole pipeline unattended but still **can't force-push or auto-approve a risky merge** — the merge/ship gate is yours to authorize.

## Key features

- **Multi-project coordinated change** — select several codebases (e.g. a service **and** its gateway) on the **Projects** screen and prompt one change; the team edits each affected repo on its own `fix/…` branch with the whole working set as shared context, so a new API in the service gets wired into the gateway in the same run — under one review → QA → ship gate.
- **Parallel, coherent builds** — fork-join across role-matched engineers in isolated git worktrees, dependency-ordered, merged; targeted rework rebuilds only what failed.
- **Verified-before-ship gate** — a deterministic ground-truth check overrides any hallucinated "looks good": an autonomous run auto-merges **only when the work was actually verified** (real build + runtime/API evidence), a **red test suite blocks the ship**, and anything unverified **halts for you** instead of merging.
- **Spec Contract + real API/response QA** — one versioned contract (UI + API items) binds spec → build → QA to the same expectations; QA issues **real HTTP requests** and asserts status / response shape / error hygiene, discovers endpoints (OpenAPI or source), and a **blocking API failure reopens the build** — with a feedback ledger (expected vs actual) fed into the next rework.
- **Real-app QA harness** — boots the app's own server (Node/Vite/static), drives headless Chromium, checks page load / render / console errors / acceptance-criteria text, captures screenshots — verdicts are grounded in what actually ran.
- **Self-improvement (compounding learning)** — every rework and failure becomes a durable **lesson** and every ship a reusable **skill**, recalled into later missions *where code is written*; skills are ranked by real effectiveness and **auto-retired** when they don't help. A per-run **cost budget** halts a run before it overspends.
- **Researcher agent** — an internet-research role (read-only) that produces **cited** briefs to ground the spec/build in current facts, hardened against SSRF + prompt-injection (fetched pages are untrusted data; secrets never leave in a query). Offline-first — inert until a research backend is configured.
- **Learning transparency UI** — see, live, which skills + memories fed each phase (and when recall was empty), the lessons/skills a run produced, per-skill effectiveness, and each mission's research brief + Spec Contract.
- **Built-in ticket board** — a Jira-like board (To Do → In Progress → In Review → QA → Done) that live-updates as the team works, plus an optional one-way **Jira mirror**.
- **Run app** — one click on a shipped mission boots the built app on a free localhost port and hands you a link to test it (then Stop to free the port).
- **Models & usage** — per-agent model binding, failover, real per-run token/cost metering, budgets and rate caps.
- **Skills & memory** — reusable skills the team auto-invokes, and a workspace memory that keeps decisions consistent across missions.
- **Integrations** — GitHub (push/PR at the ship gate, your consent required), Jira, and a real Chrome the QA agents can drive.
- **Notifications & two-way control** — opt-in **email, WhatsApp, or Slack** alerts when a mission is blocked, needs approval, ships, or halts (configured per workspace in Settings; provider credentials are stored in the DB, redacted on read). All are **two-way**: ask for a live status or resolve an approval gate without opening the app — Slack **Approve/Reject** buttons + `/shipwright`, or a WhatsApp/email reply of `approve SW-142` / `reject SW-142`. **WhatsApp is a full conversational control plane** — `start <brief>` a mission, **answer the AI's clarifying questions**, and cancel/retry, all from a chat (destructive actions confirm first). **Slack (Socket Mode), Email (IMAP), and the new no-tunnel WhatsApp Agent Platform channel need no public URL** — they work behind a firewall; Slack/WhatsApp webhooks are signature-verified, email is gated by a sender allow-list. Each channel — including the **WhatsApp Agent** (create an agent in WhatsApp, paste its API key) — is set up from **Settings → Notifications**. See [docs/notifications.md](docs/notifications.md).
- **Configurable** — set the **mission-key prefix** (e.g. `SW-142`) and the **projects directory** per workspace in Settings; bring your own model-provider credentials.

---

## Repository layout

```
apps/web/                Next.js 15 + React 19 UI (the dashboard)
services/orchestrator/   Python/FastAPI — API + agent engine (decision-graph pipeline)
services/ingester/       Go — inbound webhooks → Redis stream
packages/ui/             Shared design system (tokens + React components)
packages/api-types/      Generated TS types from the API contract
libs/py-core/            Shared Python: config, ids, models, db
db/migrations/           SQL migrations (Postgres + pgvector)
deploy/                  docker-compose + Helm for local/prod infra
```

## Prerequisites

- Node **≥22** (24 recommended) + **pnpm 11**
- **uv** (Python 3.13 is auto-provisioned)
- **Docker** (Postgres/Redis/… via compose) — optional if you point at your own Postgres
- **Go 1.24** (only to build the ingester)
- **Node on the orchestrator's PATH** if you use the QA harness / Run-app (they run built apps)

## Quick start

```bash
cp .env.example .env

# 1) Local infra (Postgres+pgvector, Redis, …)
pnpm infra:up

# 2) Orchestrator API — graph engine + Postgres persistence (recommended)
cd services/orchestrator
uv sync
SHIPWRIGHT_ENGINE=graph uv run uvicorn app.main:app --reload --port 8000    # http://localhost:8000

# 3) Web dashboard (separate terminal, Node ≥22)
export PATH="$(brew --prefix node@22)/bin:$PATH"
pnpm install
pnpm --filter @foundry/web dev                                           # http://localhost:3000
```

> **Node 22 gotcha:** the web workspace needs Node **≥22**. If your default `node` is older, switch first — `nvm use 22`, or (Homebrew) `export PATH="$(brew --prefix node@22)/bin:$PATH"`.

Then open **http://localhost:3000**, connect a model under **Models**, create a mission, and watch it build on the **Live Build** screen.

## Runtime switches

The engine, store, and LLM adapter sit behind reversible env switches:

| Switch | Values | Effect |
|---|---|---|
| `SHIPWRIGHT_ENGINE` | `legacy` · `graph` | `graph` runs the checkpointed **LangGraph** engine (`app/graph_engine.py`); `legacy` the hand-rolled loop |
| `SHIPWRIGHT_STORE` | `memory` · `postgres` | `postgres` persists everything — **`memory` wipes all data on every restart** |
| `SHIPWRIGHT_LLM_ADAPTER` | `litellm` · `legacy` | LLM provider adapter |
| `SHIPWRIGHT_SEED` | `1` · `0` | `0` disables the demo reseed on restart (keep your own agents/teams) |

**Persistence is opt-in and must be `postgres`, or runs/tickets/missions vanish on restart.** The
orchestrator's `.env` selects it:

```bash
SHIPWRIGHT_STORE=postgres
DATABASE_URL=postgresql+asyncpg://foundry_app@127.0.0.1:5432/foundry_dev
```

On `SHIPWRIGHT_ENV=local` the orchestrator **auto-migrates to head** (`alembic upgrade head`) and seeds on startup. Verify:

```bash
curl -s localhost:8000/healthz          # startup log reads: backend=postgres … autoMigrate=True
psql -h 127.0.0.1 -U foundry_app -d foundry_dev -c "select version_num from alembic_version;"   # head = 0019
```

## Tests

```bash
cd services/orchestrator && uv run ruff check . && uv run pytest                 # backend (400+ tests)
cd apps/web && pnpm --filter @foundry/web typecheck && pnpm --filter @foundry/web lint   # web
```

CI additionally runs the **Alembic migration gate** (`alembic upgrade head` → `alembic check` →
`downgrade base` → `upgrade head`) and an **RLS isolation gate** (every tenant table has FORCE row-level
security + a `ws_isolation` policy) against a real Postgres.

## Docs

- **[docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md)** — the pipeline, roles, models, autonomy, and features in depth (*what / how / when*).
- **[docs/DEMO.md](docs/DEMO.md)** — a 5–7 minute guided walkthrough / presenter's script for the product tour.
- **[docs/VERSIONS.md](docs/VERSIONS.md)** — pinned toolchain & ports.
- **[services/orchestrator/README.md](services/orchestrator/README.md)** — the API + engine internals.

## License

Licensed under the **Apache License 2.0** — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
