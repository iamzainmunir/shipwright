"""SQLAlchemy 2.0 ORM rows for the run-engine aggregates (Postgres persistence, Phase 2).

Column names match the ``foundry_core`` Pydantic field names (snake_case) so a row validates
straight into a domain model via ``Model.model_validate(row)`` (FoundryModel has
``from_attributes=True``), and a model inserts via ``Row(**model.model_dump())`` (enums dump to
their string values, matching the string columns).

This ORM is the source of truth for the Alembic migrations in ``migrations/`` (autogenerate
diffs model changes into new revisions) — the schema is created by ``alembic upgrade head``,
never ``create_all``. RLS policies and the ``mission_key_seq`` sequence are hand-added in the
initial migration (autogenerate can't infer them). It is a pragmatic subset that omits pgvector
(not installed here) — ``memories.embedding`` is JSON for now; the full doc-02 schema (pgvector +
enum types) is future work. Enum columns are plain strings holding the Canon §13.3 values.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Identity,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class MissionRow(Base):
    __tablename__ = "missions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    org_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")
    ext_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[str] = mapped_column(String(4), default="p2")
    stage: Mapped[str] = mapped_column(String(16), default="backlog", index=True)
    autonomy: Mapped[str] = mapped_column(String(16), default="supervised")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    repo_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(400), nullable=True)
    labels: Mapped[list] = mapped_column(JSON, default=list)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    project_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    project_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_ids: Mapped[list] = mapped_column(JSON, default=list)
    requirements: Mapped[str | None] = mapped_column(Text, nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentRow(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))
    role_key: Mapped[str] = mapped_column(String(48))  # 48 to fit custom-role slugs (custom_roles.key)
    model_binding: Mapped[str | None] = mapped_column(String(64), nullable=True)
    models: Mapped[list] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    status: Mapped[str] = mapped_column(String(16), default="idle")
    level: Mapped[str] = mapped_column(String(16), default="senior")
    skills: Mapped[list] = mapped_column(JSON, default=list)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)


class TeamRow(Base):
    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    members: Mapped[list] = mapped_column(JSON, default=list)  # [{agentId, accountable}]
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mission_id: Mapped[str] = mapped_column(String(40), index=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    autonomy: Mapped[str] = mapped_column(String(16), default="supervised")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cost_cents: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # v2: durable run context (build_facts, merge meta, qa_passed) — survives restarts so the
    # health gate never assumes-healthy after a crash.
    context: Mapped[dict] = mapped_column(JSON, default=dict)


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    mission_id: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    step_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(24), default="other")
    name: Mapped[str] = mapped_column(String(200))
    path: Mapped[str] = mapped_column(Text)  # relative to the artifacts root; files never in DB
    mime: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TicketRow(Base):
    __tablename__ = "tickets"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    key: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(12))
    parent_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    mission_id: Mapped[str] = mapped_column(String(40), index=True)
    subtask_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="todo", index=True)
    agent_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    agent_role: Mapped[str | None] = mapped_column(String(48), nullable=True)
    labels: Mapped[list] = mapped_column(JSON, default=list)
    priority: Mapped[str | None] = mapped_column(String(8), nullable=True)
    reopen_count: Mapped[int] = mapped_column(Integer, default=0)
    jira_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), index=True)


class TicketEventRow(Base):
    __tablename__ = "ticket_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    ticket_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(48), nullable=True)
    body: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), index=True)


class TicketLinkRow(Base):
    __tablename__ = "ticket_links"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    from_ticket: Mapped[str] = mapped_column(String(40), index=True)
    to_ticket: Mapped[str] = mapped_column(String(40), index=True)
    link_type: Mapped[str] = mapped_column(String(12), default="relates")


class JiraIssueMapRow(Base):
    __tablename__ = "jira_issue_map"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    entity_type: Mapped[str] = mapped_column(String(12))
    entity_id: Mapped[str] = mapped_column(String(40), index=True)
    jira_key: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JiraOutboxRow(Base):
    __tablename__ = "jira_outbox"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), index=True)
    ticket_id: Mapped[str] = mapped_column(String(40), index=True)
    op: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), index=True)


class CustomRoleRow(Base):
    __tablename__ = "custom_roles"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    key: Mapped[str] = mapped_column(String(48), index=True)
    label: Mapped[str] = mapped_column(String(80))
    group: Mapped[str] = mapped_column(String(16), default="other")
    skills: Mapped[list] = mapped_column(JSON, default=list)
    scope: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StepRow(Base):
    __tablename__ = "steps"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    phase: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(Text)
    agent_role: Mapped[str | None] = mapped_column(String(48), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), index=True)


class EventRow(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    mission_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    agent_role: Mapped[str | None] = mapped_column(String(48), nullable=True)
    type: Mapped[str] = mapped_column(String(16), default="system")
    text: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), index=True)


class BlockerRow(Base):
    __tablename__ = "blockers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    mission_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(8))
    detail: Mapped[str] = mapped_column(Text)
    model_connection_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)


class ModelConnectionRow(Base):
    __tablename__ = "model_connections"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    provider: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(8))
    models: Mapped[list] = mapped_column(JSON, default=list)
    endpoint: Mapped[str | None] = mapped_column(String(400), nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(400), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="disconnected")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default=text("'{}'"))


class SkillRow(Base):
    __tablename__ = "skills"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(16), default="other")
    source: Mapped[str] = mapped_column(String(16), default="built-in")
    trigger: Mapped[str | None] = mapped_column(Text, nullable=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_invoke: Mapped[bool] = mapped_column(Boolean, default=False)
    installed: Mapped[bool] = mapped_column(Boolean, default=True)
    uses: Mapped[int] = mapped_column(Integer, default=0)


class MemoryRow(Base):
    __tablename__ = "memories"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    type: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)  # pgvector in Phase 2b
    links: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IntegrationRow(Base):
    __tablename__ = "integrations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(32), default="other")
    status: Mapped[str] = mapped_column(String(16), default="disconnected")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    credential_ref: Mapped[str | None] = mapped_column(String(400), nullable=True)


class AutonomyPolicyRow(Base):
    __tablename__ = "autonomy_policies"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    autonomy: Mapped[str] = mapped_column(String(16), default="supervised")
    gates: Mapped[dict] = mapped_column(JSON, default=dict)
    spend_threshold_cents: Mapped[int] = mapped_column(Integer, default=0)  # 0 = no threshold set
    budget_cap_cents: Mapped[int] = mapped_column(Integer, default=0)  # 0 = no cap set
    max_parallel_agents: Mapped[int] = mapped_column(Integer, default=6)
    guardrails: Mapped[dict] = mapped_column(JSON, default=dict)
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    mission_key_prefix: Mapped[str] = mapped_column(String(16), default="")
    projects_dir: Mapped[str] = mapped_column(String(512), default="")
    notify_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_channels: Mapped[dict] = mapped_column(JSON, default=dict)
    notify_events: Mapped[dict] = mapped_column(JSON, default=dict)
    notify_email: Mapped[str] = mapped_column(String(320), default="")
    notify_whatsapp: Mapped[str] = mapped_column(String(32), default="")
    notify_config: Mapped[dict] = mapped_column(JSON, default=dict)


class ProjectRow(Base):
    """A registered/built codebase (local git repo) in the project registry (plan: multi-project)."""

    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(160))
    path: Mapped[str] = mapped_column(String(1024))
    source: Mapped[str] = mapped_column(String(16), default="registered")
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
