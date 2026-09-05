"""Runtime settings for the orchestrator.

Reads the environment described in `.env.example`. Field names map to
upper-cased env vars (case-insensitive); a few carry an explicit alias where the env var
name differs from the field name (e.g. `FOUNDRY_ENV`, `ORCHESTRATOR_PORT`).

Shared cross-service constants (Temporal queue names, Redis keys, tenant GUCs) live in
`foundry_core` and are imported by the modules that need them; the network/URL surface a
single service instance needs to boot lives here.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration (Pydantic v2 / pydantic-settings)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- deployment ----
    env: str = Field(
        default="local",
        validation_alias=AliasChoices("FOUNDRY_ENV", "ENV"),
        description="Deployment environment: local | dev | staging | prod.",
    )
    log_level: str = Field(default="info", description="Root log level for structlog / stdlib.")
    store: str = Field(
        default="memory",
        validation_alias=AliasChoices("FOUNDRY_STORE", "STORE"),
        description="Persistence backend: 'memory' (default) or 'postgres'.",
    )
    port: int = Field(
        default=8000,
        validation_alias=AliasChoices("ORCHESTRATOR_PORT", "PORT"),
        description="HTTP port (VERSIONS.md pins the orchestrator to 8000).",
    )

    # ---- datastores ----
    database_url: str = Field(
        default="postgresql+asyncpg://foundry:foundry_dev@localhost:5432/foundry",
        description="Async SQLAlchemy DSN (asyncpg driver).",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis (event bus / cache / streams).",
    )
    seed_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("FOUNDRY_SEED"),
        description="Seed baseline demo fixtures into empty tables. FOUNDRY_SEED=0 → start empty.",
    )
    mission_key_prefix: str = Field(
        default="M",
        validation_alias=AliasChoices("FOUNDRY_MISSION_PREFIX"),
        description="Prefix for internal mission keys (e.g. M-151). Not a Jira ticket — the source "
        "ticket, if any, is stored separately as ext_ref.",
    )

    # ---- sandbox + connectors (Phase 3) ----
    sandbox_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("FOUNDRY_SANDBOX"),
        description="Run the real git sandbox dev loop in the build phase.",
    )
    sandbox_root: str = Field(
        default="",
        validation_alias=AliasChoices("FOUNDRY_SANDBOX_ROOT"),
        description="Base dir for sandbox workspaces (empty → system temp).",
    )
    projects_root: str = Field(
        default="",
        validation_alias=AliasChoices("FOUNDRY_PROJECTS_ROOT"),
        description="Base dir where greenfield app builds are created (empty → ~/ShipwrightProjects).",
    )
    artifacts_root: str = Field(
        default="",
        validation_alias=AliasChoices("FOUNDRY_ARTIFACTS_ROOT"),
        description="Base dir for run evidence/artifact files (empty → ~/ShipwrightArtifacts). "
        "Artifact rows store paths relative to this root.",
    )
    github_repo: str = Field(default="", validation_alias=AliasChoices("GITHUB_REPO"))
    github_token: str = Field(default="", validation_alias=AliasChoices("GITHUB_TOKEN"))
    github_base: str = Field(default="main", validation_alias=AliasChoices("GITHUB_BASE"))

    # ---- engine selection (v2 strangler migration; plan 02 §6) ----
    engine: str = Field(
        default="legacy",
        validation_alias=AliasChoices("FOUNDRY_ENGINE"),
        description="Run-engine implementation: 'legacy' (hand-rolled loop) or 'graph' (LangGraph).",
    )
    run_cost_budget_cents: int = Field(
        default=0,
        validation_alias=AliasChoices("FOUNDRY_RUN_COST_BUDGET_CENTS"),
        description="Per-run LLM cost ceiling in cents; 0 = uncapped. Exceeded ⇒ halt for the user.",
    )

    # ---- ticket system (v2 Phase 5; plan 05) ----
    tickets_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("FOUNDRY_TICKETS"),
        description="Built-in Jira-like ticket board. Also toggleable per-workspace in Settings.",
    )
    ticket_key_prefix: str = Field(
        default="FT",
        validation_alias=AliasChoices("FOUNDRY_TICKET_PREFIX"),
        description="Internal ticket key prefix (FT-123) — deliberately distinct from any Jira "
        "project key so internal and mirrored keys never collide.",
    )

    # ---- QA evidence harness (v2 Phase 2; plan 06 §8) ----
    qa_evidence_enabled: bool = Field(default=True, validation_alias=AliasChoices("FOUNDRY_QA_EVIDENCE"))
    qa_video: str = Field(default="off", description="off | on_web | on_failure")
    qa_video_max_seconds: int = Field(default=30)
    qa_video_max_bytes: int = Field(default=8_000_000)
    qa_allow_npm_install: bool = Field(default=True)
    qa_npm_install_timeout_s: int = Field(default=120)
    qa_serve_ready_timeout_s: int = Field(default=15)
    qa_total_timeout_s: int = Field(default=180, description="Hard wall-clock around the whole harness.")
    qa_max_screens: int = Field(default=12)
    qa_max_log_bytes: int = Field(default=524_288)

    # ---- http / cors ----
    cors_origins: str = Field(
        default="http://localhost:3000",
        description="Comma-separated allowed origins for the web BFF (web runs on :3000).",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a list (splits the comma-separated env value)."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_local(self) -> bool:
        return self.env.lower() in {"local", "dev"}


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton (cached)."""
    return Settings()
