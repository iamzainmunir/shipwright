# Shipwright Orchestrator — FastAPI on Python 3.13, deps managed by uv.
#
# Build context is the REPO ROOT so the editable `foundry-core` path
# source (../../libs/py-core) resolves during `uv sync`:
#
#   docker build -f services/orchestrator/Dockerfile -t foundry-orchestrator .
#
FROM python:3.13-slim

# uv (pinned copy from the official distroless image).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Shared library first (its own layer so it caches independently of the service source).
COPY libs/py-core /libs/py-core
COPY services/orchestrator/pyproject.toml services/orchestrator/.python-version ./

# Resolve + install runtime deps (no dev group in the image).
RUN uv sync --no-dev --no-install-project

# Service source + Alembic migrations (needed by `alembic upgrade head` and the app's
# startup auto-migrate — db_migrate.py resolves alembic.ini/migrations relative to WORKDIR).
COPY services/orchestrator/app ./app
COPY services/orchestrator/alembic.ini ./alembic.ini
COPY services/orchestrator/migrations ./migrations

EXPOSE 8000
CMD ["uv", "run", "--no-dev", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
