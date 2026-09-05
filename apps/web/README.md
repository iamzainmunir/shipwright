# @foundry/web

The Shipwright product UI — **Next.js 15 (App Router) · React 19 · Tailwind CSS v4 · TypeScript 5.7**.
This is the front end for the autonomous AI engineering org; see `docs/VERSIONS.md`
for pinned versions/ports.

## Prerequisites

- Node 24 (repo pins `>=22`) and `pnpm` 11 — run everything from the **monorepo root** so the
  `@foundry/ui` and `@foundry/api-types` workspace packages resolve.
- The orchestrator (FastAPI, port **8000**) running if you want the dashboard health check to go
  green. It degrades gracefully when the orchestrator is down.

## Setup

```bash
# from the monorepo root
pnpm install
cp .env.example .env   # NEXT_PUBLIC_API_BASE defaults to http://localhost:8000
```

## Run

```bash
pnpm --filter @foundry/web dev        # http://localhost:3000
pnpm --filter @foundry/web build      # production build
pnpm --filter @foundry/web start      # serve the build on :3000
pnpm --filter @foundry/web lint       # eslint (next/core-web-vitals + next/typescript)
pnpm --filter @foundry/web typecheck  # tsc --noEmit
```

## Layout

```
app/
  layout.tsx          Root layout: metadata, pre-hydration theme script (dark-first), globals.css
  globals.css         @import "tailwindcss" + @foundry/ui/tokens.css; token-based base styles
  page.tsx            Landing / hero (uses @foundry/ui Button + Badge; CTA → /dashboard)
  dashboard/
    page.tsx          App shell: sidebar nav groups + topbar + Command Center placeholder
    health-status.tsx Client component: live orchestrator /healthz check via lib/api
  health/route.ts     Route Handler — the web service's own liveness probe → { status: "ok" }
lib/
  env.ts              Public config (NEXT_PUBLIC_API_BASE); safe for client components
  api.ts              Typed fetch wrapper; parses the §13.5 error envelope → throws ApiError
```

## Contracts honored

- Package name `@foundry/web`; depends on `@foundry/ui` and `@foundry/api-types` via
  `workspace:*`; `next.config.ts` transpiles both (Canon §3, integration contract).
- Styling is 100% token-driven from `@foundry/ui/tokens.css`; no color literals live here.
- JSON is camelCase on the wire; the API client parses the canonical error envelope
  `{ error: { code, message, details, requestId, retryable } }` (Canon §13.5 / doc 03 §3.2).
- Client components read only `NEXT_PUBLIC_*` — no server secrets cross the boundary
  (09-frontend.md §A4/§A5).

## Not yet wired (later phases)

Auth/BFF proxy (`app/api/*`), TanStack Query + Zustand, the WebSocket/SSE client, RBAC gating,
i18n, and the full route tree (`/w/[slug]/…`) are specified in `09-frontend.md` and land after
Phase 0 foundations.
