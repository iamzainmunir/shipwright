"""Application factory.

`create_app()` wires structlog, CORS, the request-context middleware (per-request
`X-Request-Id`), the Canon §13.5 error handlers, and the health + `/api/v1` routers.
`app = create_app()` is the ASGI entrypoint: `uvicorn app.main:app`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from foundry_core import tracing

from app import __version__
from app.api import health
from app.api.v1 import router as api_v1_router
from app.config import Settings, get_settings
from app.errors import install_error_handlers
from app.state import get_store, set_store


def configure_logging(log_level: str = "info", env: str = "local") -> None:
    """Configure structlog + stdlib logging (console renderer locally, JSON otherwise)."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer = (
        structlog.dev.ConsoleRenderer()
        if env.lower() in {"local", "dev"}
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _install_telemetry(app: FastAPI) -> bool:
    """Install OpenTelemetry (no-op unless tracing is enabled). Returns whether it is on."""
    tracing.init_telemetry("orchestrator")
    if not tracing.tracing_enabled():
        return False
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)  # auto http.server spans for every route
    return True


async def _reset_agent_liveness() -> None:
    """Clear any stale 'working' agent status left by a killed process (no runs resume on start)."""
    from foundry_core.enums import AgentStatus

    try:
        store = get_store()
        for agent in await store.list_agents():
            if str(getattr(agent.status, "value", agent.status)) != "idle":
                await store.update_agent(agent.id, status=AgentStatus.IDLE)
    except Exception:  # pragma: no cover - best-effort startup cleanup
        pass


async def _jira_drain_loop() -> None:
    """Drive the optional Jira mirror's per-workspace outbox drain (demo: the single workspace).
    Entirely inert unless the jira feature + integration credentials are configured (Rule 0)."""
    from app.jira_mirror import JiraMirror
    from app.seed import DEMO_WS

    with contextlib.suppress(asyncio.CancelledError):
        await JiraMirror(get_store()).run_forever(DEMO_WS)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.env)
    log = structlog.get_logger("foundry.orchestrator")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if settings.store == "postgres":
            from app.pgstore import PostgresStore

            # local/dev: auto-migrate to head at startup; staging/prod: the deploy runs
            # `alembic upgrade head`, so the app only seeds (plan 11 §11.4).
            pg = PostgresStore(settings.database_url, auto_migrate=settings.is_local,
                               seed=settings.seed_enabled)
            await pg.setup()
            set_store(pg)
            log.info(
                "orchestrator.store", backend="postgres",
                dsn=settings.database_url.split("@")[-1], autoMigrate=settings.is_local,
            )
        else:
            log.info("orchestrator.store", backend="memory")
        # A fresh process has no runs executing (the in-process engine doesn't resume), so any
        # agent left "working" by a killed process is stale — reset to idle so liveness is honest.
        await _reset_agent_liveness()
        # Optional Jira mirror: a per-workspace outbox drain loop. No-op (never touches the network)
        # unless the jira feature + integration credentials are configured — safe to always start.
        jira_task = asyncio.create_task(_jira_drain_loop())
        try:
            yield
        finally:
            jira_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await jira_task
            # Terminate any Run-app preview servers this process spawned, so their ports/PIDs
            # don't leak past shutdown.
            with contextlib.suppress(Exception):
                from app.state import get_preview_manager

                await get_preview_manager().stop_all()
            aclose = getattr(get_store(), "aclose", None)
            if aclose is not None:
                await aclose()

    app = FastAPI(
        title="Shipwright Orchestrator",
        version=__version__,
        summary="Public REST API + agent engine for the Shipwright autonomous engineering org.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid4().hex
        request.state.request_id = rid
        structlog.contextvars.bind_contextvars(request_id=rid)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["X-Request-Id"] = rid
        return response

    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(api_v1_router)

    tracing_on = _install_telemetry(app)

    log.info(
        "orchestrator.startup",
        env=settings.env, port=settings.port, version=__version__, tracing=tracing_on,
    )
    return app


app = create_app()
