"""foundry_core — shared building blocks for the Shipwright platform.

Distribution: ``foundry-core`` · import package: ``foundry_core``.

Re-exports the load-bearing surface (IDs, enums, domain models, settings, and the
async DB / tenant-scope helpers) so services can ``from foundry_core import Mission``.
Conforms to Canon §13: JSON is camelCase, DB is snake_case, IDs are ULIDs, tenant GUCs
are ``app.workspace_id`` / ``app.org_id``.
"""

from __future__ import annotations

from foundry_core.config import CoreSettings
from foundry_core.db import create_engine, create_session_factory, tenant_scope
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
    DefectSeverity,
    DefectStatus,
    LimitScope,
    MemoryType,
    MissionSource,
    MissionStage,
    ModelKind,
    ModelProvider,
    ModelTier,
    OrgPlan,
    Priority,
    RunStatus,
    SkillCategory,
    SkillSource,
    StepStatus,
)
from foundry_core.ids import mission_key, new_ulid
from foundry_core.models import (
    Agent,
    Blocker,
    FoundryModel,
    Memory,
    Mission,
    ModelConnection,
    Org,
    Skill,
    User,
    Workspace,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # ids
    "new_ulid",
    "mission_key",
    # config / db
    "CoreSettings",
    "create_engine",
    "create_session_factory",
    "tenant_scope",
    # enums
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
    # models
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
]
