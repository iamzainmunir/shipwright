"""Domain models (Pydantic v2) for the load-bearing entities in Canon §5.

Conforms to Canon §13: JSON is **camelCase**, the DB is snake_case. Every model derives
from :class:`FoundryModel`, which

- generates camelCase JSON aliases from snake_case field names (``workspace_id`` →
  ``workspaceId``), and
- accepts BOTH the field name and the alias on input (``populate_by_name=True``), so a row
  loaded from the (snake_case) DB and a payload from the (camelCase) API both validate.

Dump camelCase JSON with ``model.model_dump(by_alias=True)`` /
``model.model_dump_json(by_alias=True)``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from foundry_core.enums import (
    AgentLevel,
    AgentRoleKey,
    AgentStatus,
    ApprovalDecision,
    ApprovalGate,
    ArtifactKind,
    AutonomyLevel,
    BlockerKind,
    BlockerSeverity,
    ConnectionStatus,
    JiraOutboxStatus,
    MemoryType,
    MissionSource,
    MissionStage,
    ModelKind,
    ModelProvider,
    OrgPlan,
    Priority,
    RunStatus,
    SkillCategory,
    SkillSource,
    StepStatus,
    TicketEventKind,
    TicketKind,
    TicketLinkType,
    TicketStatus,
)

__all__ = [
    "FoundryModel",
    "Org",
    "Workspace",
    "User",
    "Mission",
    "Agent",
    "Blocker",
    "ModelConnection",
    "Skill",
    "Memory",
    "Run",
    "Step",
    "Event",
    "Approval",
    "Integration",
    "AutonomyPolicy",
    "Artifact",
    "AcceptanceCriterion",
    "Ticket",
    "TicketEvent",
    "TicketLink",
    "JiraIssueMap",
    "JiraOutbox",
    "CustomRole",
]


class FoundryModel(BaseModel):
    """Base model: camelCase JSON aliases, populate-by-name, enum values on dump."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        use_enum_values=True,
        from_attributes=True,
        str_strip_whitespace=True,
    )


class Org(FoundryModel):
    """Top tenant (Canon §5)."""

    id: str
    name: str
    plan: OrgPlan = OrgPlan.FREE
    created_at: datetime | None = None


class Workspace(FoundryModel):
    """A product/team inside an Org — a "Product Brain" (Canon §5)."""

    id: str
    org_id: str
    name: str
    slug: str


class User(FoundryModel):
    """A person (Canon §5)."""

    id: str
    email: str
    name: str
    avatar: str | None = None


class Mission(FoundryModel):
    """A unit of work — a ticket/task (Canon §5). User-facing key is ``FND-<n>``."""

    id: str
    key: str
    org_id: str | None = None
    workspace_id: str
    title: str
    summary: str | None = None
    source: MissionSource = MissionSource.MANUAL
    ext_ref: str | None = None
    priority: Priority = Priority.P2
    stage: MissionStage = MissionStage.BACKLOG
    autonomy: AutonomyLevel = AutonomyLevel.SUPERVISED
    progress: int = Field(default=0, ge=0, le=100)
    repo_id: str | None = None
    branch: str | None = None
    pr_url: str | None = None
    labels: list[str] = Field(default_factory=list)
    is_blocked: bool = False
    # App-builder fields: "app" = build a new project (greenfield); "change" = edit an existing
    # repo; None = legacy demo. project_path is where the app is built/edited; requirements holds
    # the full brief (e.g. extracted from an uploaded .docx/.md).
    project_kind: str | None = None
    project_path: str | None = None
    # Multi-project working set (Approach A): the Project ids a coordinated change spans. Empty ⇒
    # single-project behavior via `project_path`. `project_path` stays the primary/home repo.
    project_ids: list[str] = Field(default_factory=list)
    requirements: str | None = None
    team_id: str | None = None  # the Team staffing this mission (None = default org roster)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("project_ids", "labels", mode="before")
    @classmethod
    def _coerce_none_list(cls, v: object) -> object:
        """A JSON list column can be NULL for rows created before the field existed — read as ``[]``."""
        return v if v is not None else []


class Agent(FoundryModel):
    """A team-member instance (Canon §5)."""

    id: str
    org_id: str | None = None
    workspace_id: str
    name: str
    # Built-in role keys are the AgentRoleKey values, but a workspace may define CUSTOM roles
    # (see :class:`CustomRole`), so this is a free-form string keyed by role slug.
    role_key: str
    model_binding: str | None = None
    models: list[str] = Field(default_factory=list)  # ordered: primary + fallbacks (failover)
    status: AgentStatus = AgentStatus.IDLE
    level: AgentLevel = AgentLevel.SENIOR
    skills: list[str] = Field(default_factory=list)
    system_prompt: str | None = None
    stats: dict[str, int] = Field(
        default_factory=lambda: {"shipped": 0, "prs": 0, "reviews": 0, "merged": 0}
    )


class Team(FoundryModel):
    """A named group that staffs a project — role→agent assignments (Canon §5, teams).

    Agents are org-level and may belong to many teams (shared developers). ``members`` holds
    ``{"agentId", "accountable"}`` entries; the *accountable* member for a decision role has the
    final verdict on this team's projects (one Accountable per role — others advise)."""

    id: str
    org_id: str | None = None
    workspace_id: str
    name: str
    description: str | None = None
    members: list[dict] = Field(default_factory=list)  # [{agentId, accountable}]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Blocker(FoundryModel):
    """A pause on a Mission needing human action (Canon §5; §13.11 rename to model_connection_id)."""

    id: str
    org_id: str | None = None
    workspace_id: str | None = None
    mission_id: str
    kind: BlockerKind
    severity: BlockerSeverity
    detail: str
    model_connection_id: str | None = None
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None


class ModelConnection(FoundryModel):
    """A provider configuration (Canon §5). ``credential_ref`` is a Vault path, never a secret."""

    id: str
    org_id: str | None = None
    workspace_id: str
    provider: ModelProvider
    kind: ModelKind
    models: list[str] = Field(default_factory=list)
    endpoint: str | None = None
    credential_ref: str | None = None
    status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    is_primary: bool = False
    config: dict = Field(default_factory=dict)  # usage limits / restrictions (Phase 11)


class Skill(FoundryModel):
    """A reusable capability an agent can invoke (Canon §5)."""

    id: str
    org_id: str | None = None
    workspace_id: str
    name: str
    description: str
    category: SkillCategory = SkillCategory.OTHER
    source: SkillSource = SkillSource.BUILT_IN
    trigger: str | None = None
    instructions: str | None = None
    auto_invoke: bool = False
    installed: bool = True
    uses: int = 0


class Memory(FoundryModel):
    """A persisted fact with an optional embedding vector (Canon §5; dim 1536 per §13.10)."""

    id: str
    org_id: str | None = None
    workspace_id: str
    type: MemoryType
    title: str
    body: str
    embedding: list[float] | None = None
    links: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


# --- Run-engine domain (Canon §5: Run/Step/Event/Approval) -----------------------

class Run(FoundryModel):
    """One execution of a Mission by the team — a durable workflow instance (Canon §5)."""

    id: str
    mission_id: str
    workspace_id: str
    status: RunStatus = RunStatus.QUEUED
    autonomy: AutonomyLevel = AutonomyLevel.SUPERVISED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cost_cents: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    provider: str | None = None  # LLM provider this run executed on (e.g. "ollama", "anthropic")
    model: str | None = None  # concrete model id the run used (e.g. "gemma2:2b")
    error: str | None = None
    # Durable run context (v2): ground-truth facts that must survive a restart — build_facts
    # (files/steps/tests/healthy/why), merge meta, qa_passed — so the health gate never has to
    # "assume healthy" after a crash (the v1 in-memory-dict hole).
    context: dict = Field(default_factory=dict)


class Step(FoundryModel):
    """A phase/action inside a Run (Canon §5). ``phase`` is a run phase (Canon §6)."""

    id: str
    run_id: str
    phase: str
    title: str
    agent_role: AgentRoleKey | None = None
    status: StepStatus = StepStatus.QUEUED
    detail: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None


class Artifact(FoundryModel):
    """A produced file the run keeps as evidence/deliverable — screenshots, videos, QA reports,
    logs, traces (kinds in :class:`ArtifactKind`). Files live on disk under the artifacts root;
    the row stores the relative ``path`` + ``sha256`` (never blobs). ``meta`` carries per-kind
    context (criterion_id, url, seq, evidence_quality, duration_ms…)."""

    id: str
    workspace_id: str
    mission_id: str
    run_id: str
    step_id: str | None = None
    kind: ArtifactKind = ArtifactKind.OTHER
    name: str
    path: str  # relative to the artifacts root: <mission_id>/<run_id>/qa/…
    mime: str = "application/octet-stream"
    size_bytes: int = 0
    sha256: str = ""
    meta: dict = Field(default_factory=dict)
    created_at: datetime | None = None


class AcceptanceCriterion(FoundryModel):
    """One acceptance criterion from the spec, optionally machine-checkable (v2 QA harness).

    The spec agent emits these at spec time; the QA harness executes the machine block
    deterministically (no LLM in the harness). A criterion without route/expectations
    degrades to screenshot-only evidence."""

    id: str  # short stable id within the spec, e.g. "AC1"
    criterion: str  # human sentence: "User can add a task"
    route: str | None = None  # page to visit, e.g. "/"
    expect_text: list[str] = Field(default_factory=list)
    expect_selector: list[str] = Field(default_factory=list)
    # "blocking" (core — a failure sends the build back) | "non_blocking" (minor — QA may forward the
    # build with a tracked follow-up). Defaults to blocking so an UNMARKED failure is fail-safe.
    severity: str = "blocking"


class Ticket(FoundryModel):
    """A work item on Shipwright's built-in Jira-like board (plan 05 §1). One canonical model, two
    views: this internal board and (optionally) the Jira mirror. Epics map to missions, Stories to
    build subtasks, Bugs to QA findings. ``key`` uses the internal prefix (FT-123), deliberately
    distinct from the mission/Jira keys so they never read as the same ticket."""

    id: str
    workspace_id: str
    key: str  # FT-123 (internal, configurable prefix)
    kind: TicketKind
    parent_id: str | None = None  # story/bug → epic
    mission_id: str
    subtask_id: str | None = None  # stories: the build subtask id
    run_id: str | None = None      # bugs: the QA run that found it
    title: str
    description: dict = Field(default_factory=dict)  # {goal, acceptance[], dod[], impl_notes}
    status: TicketStatus = TicketStatus.TODO
    agent_name: str | None = None
    agent_role: str | None = None
    labels: list[str] = Field(default_factory=list)
    priority: str | None = None
    reopen_count: int = 0
    jira_key: str | None = None  # populated by the mirror once mapped (plan 06)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TicketEvent(FoundryModel):
    """One entry in a ticket's activity feed (plan 05 §1). Every transition ALWAYS records
    who/what/why — that is the CEO-story requirement, enforced in the consumer, not the prompt."""

    id: str
    workspace_id: str
    ticket_id: str
    kind: TicketEventKind
    from_status: str | None = None
    to_status: str | None = None
    actor_name: str | None = None
    actor_role: str | None = None
    body: dict = Field(default_factory=dict)  # comment doc / evidence refs (artifact_ids) / reason
    created_at: datetime | None = None


class TicketLink(FoundryModel):
    """A directed relationship between tickets (bug blocks story, story relates to story)."""

    id: str
    workspace_id: str
    from_ticket: str
    to_ticket: str
    link_type: TicketLinkType = TicketLinkType.RELATES


class CustomRole(FoundryModel):
    """A user-defined agent role (beyond the built-in catalog), scoped to a workspace — like a real
    org adding roles (e.g. Product Coordinator). Carries its own default skill set, applied to any
    agent created with it, exactly like the built-in roles. ``group`` places it in the team taxonomy
    (exec | product | eng | devops | quality | other)."""

    id: str
    workspace_id: str
    key: str            # slug, e.g. "product-coordinator"
    label: str          # human name, e.g. "Product Coordinator"
    group: str = "other"
    skills: list[str] = Field(default_factory=list)
    scope: str = ""
    created_at: datetime | None = None


class JiraIssueMap(FoundryModel):
    """Idempotency map for the Jira mirror (plan 05 §4): a Shipwright entity → its Jira issue key.
    UNIQUE on (workspace, entity_type, entity_id) prevents a double-create across retries/crashes."""

    id: str
    workspace_id: str
    entity_type: str  # epic | story | bug (the ticket kind)
    entity_id: str    # the ticket id
    jira_key: str
    created_at: datetime | None = None


class JiraOutbox(FoundryModel):
    """A transactional-outbox row: one pending Jira write, enqueued in the same store txn as the
    ticket write (plan 05 §4). A per-workspace worker drains these serially with retry/backoff, so no
    pipeline state ever reads from, waits on, or fails because of Jira (Rule 0). ``idempotency_key``
    (e.g. the source ticket_event id) is UNIQUE — a replayed event never enqueues twice."""

    id: str
    workspace_id: str
    idempotency_key: str
    ticket_id: str
    op: str            # create | transition | comment | evidence | link | reopen
    payload: dict = Field(default_factory=dict)  # self-contained snapshot for the worker
    status: JiraOutboxStatus = JiraOutboxStatus.PENDING
    attempts: int = 0
    last_error: str | None = None
    next_attempt_at: datetime | None = None  # None ⇒ ready now
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Event(FoundryModel):
    """An append-only activity record — drives the live feed & console (Canon §5/§7)."""

    id: str
    run_id: str | None = None
    mission_id: str | None = None
    workspace_id: str
    agent_role: AgentRoleKey | None = None
    type: str = "system"  # code|qa|review|ledger|spec|deploy|system (Canon §7)
    text: str
    payload: dict = Field(default_factory=dict)
    ts: datetime | None = None


class Approval(FoundryModel):
    """A recorded human decision on a gated action (Canon §5)."""

    id: str
    mission_id: str
    run_id: str | None = None
    blocker_id: str | None = None
    gate: ApprovalGate
    decision: ApprovalDecision | None = None
    actor: str | None = None
    note: str | None = None
    created_at: datetime | None = None
    decided_at: datetime | None = None


class Integration(FoundryModel):
    """A connected 3rd-party tool (Canon §5). `category` groups the UI; not a secret store."""

    id: str
    org_id: str | None = None
    workspace_id: str
    kind: str  # jira|linear|github|gitlab|slack|chrome|sentry|figma|postgres|vercel
    name: str
    category: str = "other"
    status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    config: dict = Field(default_factory=dict)
    credential_ref: str | None = None


def _default_gates() -> dict[str, bool]:
    # Canon §6 supervised default: hold merge/deploy/spend/delete/account; external off.
    return {"merge": True, "deploy": True, "spend": True,
            "external": False, "delete": True, "account": True}


def _default_guardrails() -> dict[str, bool]:
    return {"blockCrossTenant": True, "requireTests": True}


def _default_features() -> dict[str, bool]:
    # Additive product features toggled in Settings → Features. Ticket board defaults ON (zero setup).
    return {"tickets": True, "jira": False}


class AutonomyPolicy(FoundryModel):
    """Workspace autonomy + approval-gate config (Canon §5 Setting/AutonomyPolicy)."""

    id: str
    workspace_id: str
    autonomy: AutonomyLevel = AutonomyLevel.SUPERVISED
    gates: dict[str, bool] = Field(default_factory=_default_gates)
    spend_threshold_cents: int = 0  # 0 = no threshold set (unconfigured)
    budget_cap_cents: int = 0  # 0 = no monthly cap set (unconfigured)
    max_parallel_agents: int = 6
    guardrails: dict[str, bool] = Field(default_factory=_default_guardrails)
    features: dict[str, bool] = Field(default_factory=_default_features)
    # User-configurable defaults (empty ⇒ fall back to the global config default):
    mission_key_prefix: str = ""  # e.g. "M" ⇒ M-151; empty ⇒ SHIPWRIGHT_MISSION_PREFIX
    projects_dir: str = ""  # where greenfield apps are built; empty ⇒ ~/ShipwrightProjects
    # Notifications (opt-in, per-workspace). Provider credentials come from the environment;
    # only the non-secret preferences (which channels/events, recipient) are stored here.
    notify_enabled: bool = False
    notify_channels: dict[str, bool] = Field(default_factory=dict)  # email · whatsapp_twilio · whatsapp_meta · slack
    notify_events: dict[str, bool] = Field(default_factory=dict)  # blocker · completed · failed · approval
    notify_email: str = ""  # recipient email address (for the email channel)
    notify_whatsapp: str = ""  # recipient WhatsApp number, E.164 e.g. +15551234567
    # Provider credentials + settings, stored in the DB (configured in the UI, not .env). Secret
    # keys (…Password/…Token/…Webhook) are write-only over the API — redacted on read, merged on write.
    notify_config: dict = Field(default_factory=dict)

    @field_validator(
        "gates", "guardrails", "features", "notify_channels", "notify_events", "notify_config",
        mode="before",
    )
    @classmethod
    def _coerce_none_dict(cls, v: object) -> object:
        """A JSON column can be NULL for rows created before the field existed — read NULL as ``{}``."""
        return v if v is not None else {}


class Project(FoundryModel):
    """A codebase the org can build in or edit — a local git repo. Either auto-registered when a
    mission builds to it (``source="built"``) or added by the user (``source="registered"``).
    Projects are the working set a multi-target mission coordinates a change across."""

    id: str
    workspace_id: str
    name: str
    slug: str
    path: str
    source: str = "registered"  # "built" | "registered"
    created_at: datetime | None = None
    last_activity_at: datetime | None = None
