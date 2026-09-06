"""Seed data shared by both stores (mirrors the mockup / db/seeds).

One source of truth so the in-memory and Postgres stores start from the same fixtures.
"""

from __future__ import annotations

from datetime import UTC, datetime

from foundry_core.enums import (
    AgentRoleKey,
    AgentStatus,
    AutonomyLevel,
    ConnectionStatus,
    MemoryType,
    MissionSource,
    MissionStage,
    ModelKind,
    ModelProvider,
    Priority,
    SkillCategory,
)
from foundry_core.ids import new_ulid
from foundry_core.models import (
    Agent,
    AutonomyPolicy,
    Integration,
    Memory,
    Mission,
    ModelConnection,
    Skill,
)

DEMO_WS = "01J8Z9WORKSPACEDEMO000001"
DEMO_ORG = "01J8Z9ORG0000000000000001"


def _now() -> datetime:
    return datetime.now(UTC)


def seed_agents() -> list[Agent]:
    # (name, role, model_binding, skills) — skills are the agent's real capability tags
    # (its toolbelt), not usage metrics. The engine flips status/stats as it runs.
    spec = [
        ("Nova", AgentRoleKey.PM, "claude-sonnet-5",
         ["prd", "stories", "prioritization", "stakeholders"]),
        ("Kai", AgentRoleKey.CTO, "claude-opus-4-8",
         ["architecture", "security-review", "tech-strategy", "scaling"]),
        ("Ada", AgentRoleKey.BACKEND, "claude-sonnet-5",
         ["api-design", "tdd", "databases", "refactoring"]),
        ("Ivy", AgentRoleKey.QA, "claude-haiku-4-5",
         ["acceptance-tests", "regression", "browser-qa", "bug-triage"]),
        ("Rex", AgentRoleKey.DEVOPS, "claude-haiku-4-5",
         ["ci-cd", "deploys", "observability", "rollback"]),
    ]
    return [
        Agent(
            id=new_ulid(), org_id=DEMO_ORG, workspace_id=DEMO_WS,
            name=name, role_key=role, model_binding=model, status=AgentStatus.IDLE,
            skills=skills,
        )
        for name, role, model, skills in spec
    ]


def seed_missions() -> list[Mission]:
    return [
        Mission(
            id=new_ulid(), key="FND-142", org_id=DEMO_ORG, workspace_id=DEMO_WS,
            title="Scope the surcharge to the caller's tenant",
            summary=(
                "A by-id read used the URL id, not the caller's tenant; add tenant scoping "
                "and a regression test."
            ),
            source=MissionSource.JIRA, ext_ref="NPD-11402", priority=Priority.P0,
            stage=MissionStage.BACKLOG, autonomy=AutonomyLevel.SUPERVISED, progress=0,
            labels=["security", "billing"],
            created_at=_now(), updated_at=_now(),
        ),
        Mission(
            id=new_ulid(), key="FND-150", org_id=DEMO_ORG, workspace_id=DEMO_WS,
            title="Add CSV export to the usage dashboard",
            summary="Operators want a monthly cost export from the usage records view.",
            source=MissionSource.MANUAL, priority=Priority.P2, stage=MissionStage.BACKLOG,
            autonomy=AutonomyLevel.SUPERVISED, labels=["billing"],
            created_at=_now(), updated_at=_now(),
        ),
    ]


def seed_model_connections() -> list[ModelConnection]:
    return [
        ModelConnection(
            id=new_ulid(), org_id=DEMO_ORG, workspace_id=DEMO_WS,
            provider=ModelProvider.ANTHROPIC, kind=ModelKind.CLOUD,
            models=["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"],
            credential_ref="secret/data/shipwright/{org}/{ws}/models/anthropic#api_key",
            status=ConnectionStatus.CONNECTED, is_primary=True,
        ),
        ModelConnection(
            id=new_ulid(), org_id=DEMO_ORG, workspace_id=DEMO_WS,
            provider=ModelProvider.OLLAMA, kind=ModelKind.LOCAL,
            models=["qwen2.5:7b", "gemma2:2b"],
            endpoint="http://localhost:11434", status=ConnectionStatus.CONNECTED,
        ),
    ]


def seed_skills() -> list[Skill]:
    return [
        Skill(
            id=new_ulid(), org_id=DEMO_ORG, workspace_id=DEMO_WS, name="document",
            description="Run the intake interview, lock a spec, derive stories.",
            category=SkillCategory.PRODUCT, auto_invoke=True, uses=0,
        ),
        Skill(
            id=new_ulid(), org_id=DEMO_ORG, workspace_id=DEMO_WS, name="test-driven-dev",
            description="Write a failing test first, then the minimum code to pass it.",
            category=SkillCategory.ENGINEERING, auto_invoke=True, uses=0,
        ),
    ]


def seed_integrations() -> list[Integration]:
    # Available integrations, all disconnected until the user connects them (a real DB
    # state change). Nothing claims to be connected without an explicit connect action.
    spec = [
        ("jira", "Jira", "Ticketing"),
        ("github", "GitHub", "Code"),
        ("slack", "Slack", "Comms"),
        ("chrome", "Chrome Browser", "QA & Runtime"),
        ("sentry", "Sentry", "Observability"),
        ("linear", "Linear", "Ticketing"),
        ("figma", "Figma", "Design"),
        ("vercel", "Vercel", "Deploy"),
    ]
    return [
        Integration(id=new_ulid(), org_id=DEMO_ORG, workspace_id=DEMO_WS,
                    kind=kind, name=name, category=cat,
                    status=ConnectionStatus.DISCONNECTED)
        for kind, name, cat in spec
    ]


def default_settings() -> AutonomyPolicy:
    return AutonomyPolicy(id=new_ulid(), workspace_id=DEMO_WS)


def seed_memories() -> list[Memory]:
    return [
        Memory(
            id=new_ulid(), org_id=DEMO_ORG, workspace_id=DEMO_WS, type=MemoryType.PROJECT,
            title="Tenant scoping is non-negotiable",
            body="Every by-id read/write must pin the caller's workspace/tenant.",
        )
    ]
