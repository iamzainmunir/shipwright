# syntax=docker/dockerfile:1
# Shipwright orchestrator (services/orchestrator) — Python 3.13 / FastAPI + Uvicorn, managed by uv.
# CANONICAL Dockerfile for the orchestrator (kept here in deploy/; the source lives in services/orchestrator).
# BUILD CONTEXT = repo root (it depends on libs/py-core via [tool.uv.sources] foundry-core = { path = "../../libs/py-core", editable = true }):
#   docker build -f deploy/docker/orchestrator.Dockerfile -t foundry/orchestrator .
# Serves on :8000 (docs/VERSIONS.md). The same image also runs the workflow/activity workers with a different command.

FROM python:3.13-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir uv
WORKDIR /app

# ---- resolve + install dependencies (editable foundry-core needs the sibling path present) ----
FROM base AS build
COPY libs/py-core /app/libs/py-core
COPY services/orchestrator /app/services/orchestrator
WORKDIR /app/services/orchestrator
RUN uv sync --frozen --no-dev || uv sync --no-dev

# ---- runtime ----
FROM base AS runtime
ENV ORCHESTRATOR_PORT=8000
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app /app
WORKDIR /app/services/orchestrator
EXPOSE 8000
# ASGI entrypoint is owned by services/orchestrator (app package at app/api/). Adjust the module path there if it differs.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
