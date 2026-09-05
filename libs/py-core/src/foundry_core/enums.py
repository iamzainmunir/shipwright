"""Canonical enums — values are locked by 00-CANON §13.3 (ratified) and §5/§6.

Every member VALUE is exactly the Canon string so it round-trips to the Postgres
``CREATE TYPE`` enums in 02-data-model §2 (DB stores these lowercase forms). These are
``str``-mixin enums, so ``RunStatus.running == "running"`` and they serialize verbatim in
JSON via Pydantic.

Note on ``Priority``: Canon §13.3 stores ``p0..p3`` (lowercase) in the DB and exposes
``P0..P3`` (uppercase) on the wire; the BFF maps between them. This library models the
STORED form (lowercase), consistent with every other enum here.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "RunStatus",
    "ModelTier",
    "SkillCategory",
    "SkillSource",
    "ConnectionStatus",
    "ApprovalDecision",
    "ApprovalGate",
    "LimitScope",
    "MissionSource",
    "MissionStage",
    "DefectSeverity",
    "DefectStatus",
    "ArtifactKind",
    "Priority",
    "StepStatus",
    "BlockerKind",
    "BlockerSeverity",
    "AutonomyLevel",
    "AgentRoleKey",
    "AgentStatus",
    "AgentLevel",
    "OrgPlan",
    "ModelProvider",
    "ModelKind",
    "MemoryType",
]


class StrEnum(str, Enum):
    """A ``str``-valued enum whose ``str()`` is the member value (not ``Class.MEMBER``)."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


# --- Canon §13.3 ratified enums -------------------------------------------------

class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ModelTier(StrEnum):
    FRONTIER = "frontier"
    BALANCED = "balanced"
    FAST = "fast"
    LOCAL = "local"


class SkillCategory(StrEnum):
    PRODUCT = "product"
    ENGINEERING = "engineering"
    QUALITY = "quality"
    SECURITY = "security"
    DEVOPS = "devops"
    DESIGN = "design"
    OTHER = "other"


class ConnectionStatus(StrEnum):
    """Also used for integration status (Canon §13.3)."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    EXPIRED = "expired"


class ApprovalDecision(StrEnum):
    """API body verb form (Canon §13.3)."""

    APPROVE = "approve"
    REJECT = "reject"


class LimitScope(StrEnum):
    WORKSPACE = "workspace"
    MODEL_CONNECTION = "model_connection"


class MissionSource(StrEnum):
    JIRA = "jira"
    LINEAR = "linear"
    GITHUB = "github"
    MANUAL = "manual"
    SENTRY = "sentry"
    GITLAB = "gitlab"


class DefectSeverity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    TRIVIAL = "trivial"


class DefectStatus(StrEnum):
    OPEN = "open"
    ROUTED = "routed"
    FIXED = "fixed"
    WONTFIX = "wontfix"
    DUPLICATE = "duplicate"


class ArtifactKind(StrEnum):
    DIFF = "diff"
    PATCH = "patch"
    PDF = "pdf"
    LOG = "log"
    SCREENSHOT = "screenshot"
    VIDEO = "video"
    TRACE = "trace"
    REPORT = "report"
    COVERAGE = "coverage"
    INTAKE = "intake"
    LEDGER = "ledger"
    SPEC = "spec"
    STORIES = "stories"
    PLAN = "plan"
    TEST_REPORT = "test_report"
    SECURITY_REPORT = "security_report"
    QA_REPORT = "qa_report"
    REALITY = "reality"
    DESIGN_CONTEXT = "design_context"
    PREVIEW_DEPLOY = "preview_deploy"
    EVAL_REPORT = "eval_report"
    OTHER = "other"


class TicketKind(StrEnum):
    """Work-item type on the built-in board (plan 05 §1)."""

    EPIC = "epic"      # one per mission
    STORY = "story"    # one per build subtask
    BUG = "bug"        # one per QA finding


class TicketStatus(StrEnum):
    """Board columns + the reopened state (plan 05 §3). ``reopened`` is a state rendered in the
    To Do column with a red badge, not its own column."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    QA = "qa"
    IN_REVIEW = "in_review"
    DONE = "done"
    REOPENED = "reopened"
    BLOCKED = "blocked"   # work paused — force-stopped run or awaiting a human (its own board column)


class TicketEventKind(StrEnum):
    """Activity-feed entry types (plan 05 §1). Every transition ALWAYS carries who/what/why."""

    CREATED = "created"
    TRANSITIONED = "transitioned"
    COMMENTED = "commented"
    EVIDENCE_ATTACHED = "evidence_attached"
    REOPENED = "reopened"
    LINKED = "linked"
    REWORKED = "reworked"
    SHIPPED = "shipped"
    SYNC_ERROR = "sync_error"  # a consumer/mirror failure, surfaced never silent (Rule 0)


class TicketLinkType(StrEnum):
    BLOCKS = "blocks"
    RELATES = "relates"
    FIXES = "fixes"


class JiraOutboxStatus(StrEnum):
    """Transactional-outbox row state for the Jira mirror (plan 05 §4)."""

    PENDING = "pending"   # awaiting the worker (respecting next_attempt_at backoff)
    DONE = "done"         # delivered to Jira
    DEAD = "dead"         # permanently failed (400 / attempts exhausted) — replayable from the UI


class Priority(StrEnum):
    """Stored form (Canon §13.3): DB keeps ``p0..p3``; wire exposes ``P0..P3``."""

    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"


# --- Canon §5 / §6 entity & lifecycle enums -------------------------------------

class MissionStage(StrEnum):
    """Kanban columns (Canon §6)."""

    BACKLOG = "backlog"
    SPEC = "spec"
    BUILDING = "building"
    QA = "qa"
    REVIEW = "review"
    SHIPPED = "shipped"
    STOPPED = "stopped"  # force-stopped by the user; retry resumes it


class StepStatus(StrEnum):
    QUEUED = "queued"
    ACTIVE = "active"
    DONE = "done"
    BLOCKED = "blocked"
    GATED = "gated"


class BlockerKind(StrEnum):
    APPROVAL = "approval"
    QUESTION = "question"
    LIMIT = "limit"
    TOKEN = "token"
    BUDGET = "budget"
    ERROR = "error"
    DEPENDENCY = "dependency"


class BlockerSeverity(StrEnum):
    """``warn`` = approval/question/dependency · ``block`` = limit/token/budget/error (Canon §6)."""

    WARN = "warn"
    BLOCK = "block"


class ApprovalGate(StrEnum):
    MERGE = "merge"
    DEPLOY = "deploy"
    SPEND = "spend"
    EXTERNAL = "external"
    DELETE = "delete"
    ACCOUNT = "account"


class AutonomyLevel(StrEnum):
    MANUAL = "manual"
    ASSISTED = "assisted"
    SUPERVISED = "supervised"
    AUTONOMOUS = "autonomous"


class AgentRoleKey(StrEnum):
    CEO = "ceo"
    CTO = "cto"
    PM = "pm"
    BA = "ba"
    BACKEND = "backend"
    FRONTEND = "frontend"
    QA = "qa"
    DEVOPS = "devops"
    DESIGNER = "designer"
    SECURITY = "security"
    CUSTOM = "custom"


# --- Supporting entity enums (02-data-model §2; needed by the domain models) -----

class AgentStatus(StrEnum):
    WORKING = "working"
    REVIEW = "review"
    IDLE = "idle"
    BLOCKED = "blocked"


class AgentLevel(StrEnum):
    JUNIOR = "junior"
    SENIOR = "senior"
    PRINCIPAL = "principal"
    STRATEGIC = "strategic"


class OrgPlan(StrEnum):
    FREE = "free"
    TEAM = "team"
    BUSINESS = "business"
    ENTERPRISE = "enterprise"


class ModelProvider(StrEnum):
    ANTHROPIC = "anthropic"
    CLAUDE_CLI = "claude_cli"  # the local Claude Code binary (subscription seat, driven as a subprocess)
    OPENAI = "openai"
    GOOGLE = "google"
    OLLAMA = "ollama"
    XAI = "xai"
    GROQ = "groq"
    MISTRAL = "mistral"
    DEEPSEEK = "deepseek"
    TOGETHER = "together"
    COHERE = "cohere"
    AZURE = "azure"
    # Popular OpenAI-compatible providers (one adapter serves them all — see providers/catalog.py).
    OPENROUTER = "openrouter"   # aggregator: one key → hundreds of models across vendors
    PERPLEXITY = "perplexity"
    FIREWORKS = "fireworks"
    DEEPINFRA = "deepinfra"
    CEREBRAS = "cerebras"
    NEBIUS = "nebius"
    OPENAI_COMPATIBLE = "openai_compatible"  # any other OpenAI-compatible endpoint (custom base URL)
    CUSTOM = "custom"


class ModelKind(StrEnum):
    CLOUD = "cloud"
    LOCAL = "local"


class SkillSource(StrEnum):
    BUILT_IN = "built-in"
    CUSTOM = "custom"
    MARKETPLACE = "marketplace"


class MemoryType(StrEnum):
    PROJECT = "project"
    FEEDBACK = "feedback"
    REFERENCE = "reference"
    USER = "user"
