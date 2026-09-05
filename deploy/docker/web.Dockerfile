# syntax=docker/dockerfile:1
# Shipwright web (apps/web) — Next.js 15 / React 19, pnpm workspace build.
# BUILD CONTEXT = repo root (the app depends on workspace packages @foundry/ui + @foundry/api-types):
#   docker build -f deploy/docker/web.Dockerfile -t foundry/web .
# Serves on :3000 (docs/VERSIONS.md).

FROM node:24-alpine AS base
ENV PNPM_HOME=/pnpm
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable
WORKDIR /repo

# ---- install + build the whole workspace (needed for workspace:* cross-refs) ----
FROM base AS build
ENV NEXT_TELEMETRY_DISABLED=1
COPY . .
RUN pnpm install --frozen-lockfile
RUN pnpm --filter @foundry/web... build

# ---- runtime ----
FROM base AS runtime
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV WEB_PORT=3000
COPY --from=build /repo /repo
WORKDIR /repo/apps/web
EXPOSE 3000
# apps/web defines "start": "next start -p 3000" (owned by the web subtree).
CMD ["pnpm", "start"]
