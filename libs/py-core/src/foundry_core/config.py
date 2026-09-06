"""Shared settings (pydantic-settings).

:class:`CoreSettings` is the common env surface every Python service (orchestrator, runner,
qa-runner workers) mixes in or subclasses. Values load from process env and an optional
``.env`` file, all prefixed ``SHIPWRIGHT_`` — e.g. ``SHIPWRIGHT_DATABASE_URL``. Ports default to
the values pinned in ``docs/VERSIONS.md``.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["CoreSettings"]


class CoreSettings(BaseSettings):
    """Common environment for Shipwright Python services.

    Subclass this in a service to add service-specific fields:

        class OrchestratorSettings(CoreSettings):
            temporal_host: str = "localhost:7233"
    """

    model_config = SettingsConfigDict(
        env_prefix="SHIPWRIGHT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Runtime -----------------------------------------------------------------
    environment: str = "development"  # development | staging | production
    log_level: str = "INFO"
    service_name: str = "shipwright-core"

    # --- Datastores (see docs/VERSIONS.md) --------------------------------------
    # SQLAlchemy async URL; psycopg3 driver by default (Postgres 16 + pgvector).
    database_url: str = "postgresql+psycopg://shipwright:shipwright@localhost:5432/shipwright"
    redis_url: str = "redis://localhost:6379/0"

    # --- Tenancy defaults (GUCs are app.workspace_id / app.org_id — Canon §13.2) -
    # Optional bootstrap context for single-tenant local scripts; the request path
    # always sets tenant context explicitly via db.tenant_scope().
    default_org_id: str | None = None
    default_workspace_id: str | None = None

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"
