"""Wipe everything and seed a single, clean demo: local Ollama models, ONE team, and ONE
project ("TODO app simple version") — plus the integrations list (disconnected). No other
missions, runs, skills, memories, extra models, or teams.

Run (from services/orchestrator):
  DATABASE_URL="postgresql+asyncpg://foundry_app@127.0.0.1:5432/foundry_dev" \\
    uv run python scripts/seed_todo_demo.py --yes
"""
from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.pgstore import PostgresStore  # noqa: E402
from app.seed import DEMO_ORG, DEMO_WS  # noqa: E402
from foundry_core.enums import (  # noqa: E402
    AgentRoleKey,
    AgentStatus,
    AutonomyLevel,
    ConnectionStatus,
    MissionSource,
    MissionStage,
    ModelKind,
    ModelProvider,
    Priority,
)
from foundry_core.ids import new_ulid  # noqa: E402
from foundry_core.models import Agent, Integration, Mission, ModelConnection, Team  # noqa: E402
from sqlalchemy import text  # noqa: E402

TABLES = [
    "missions", "runs", "steps", "events", "blockers",
    "agents", "teams", "model_connections", "skills", "memories",
    "integrations", "autonomy_policies",
]

# Local Ollama models the whole org runs on (primary + failover order).
OLLAMA_MODELS = ["qwen2.5:7b", "gemma2:2b"]

ROSTER = [
    ("Nova", AgentRoleKey.PM, ["prd", "stories", "prioritization"]),
    ("Kai", AgentRoleKey.CTO, ["architecture", "security-review", "tech-strategy"]),
    ("Ada", AgentRoleKey.BACKEND, ["api-design", "tdd", "refactoring"]),
    ("Fin", AgentRoleKey.FRONTEND, ["ui", "components", "accessibility"]),
    ("Ivy", AgentRoleKey.QA, ["acceptance-tests", "regression", "browser-qa"]),
    ("Rex", AgentRoleKey.DEVOPS, ["ci-cd", "deploys", "rollback"]),
]

INTEGRATIONS = [
    ("jira", "Jira", "Ticketing"), ("github", "GitHub", "Code"), ("slack", "Slack", "Comms"),
    ("chrome", "Chrome Browser", "QA & Runtime"), ("sentry", "Sentry", "Observability"),
    ("linear", "Linear", "Ticketing"), ("figma", "Figma", "Design"), ("vercel", "Vercel", "Deploy"),
]


async def main() -> None:
    if "--yes" not in sys.argv:
        print("Refusing to wipe without --yes. This deletes ALL data. Re-run with --yes.")
        return

    store = PostgresStore(get_settings().database_url, auto_migrate=False, seed=False)
    await store.setup()
    now = datetime.now(UTC)

    async with store.engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        await conn.execute(text("ALTER SEQUENCE mission_key_seq RESTART WITH 151"))

    await store.add_model_connection(ModelConnection(
        id=new_ulid(), org_id=DEMO_ORG, workspace_id=DEMO_WS,
        provider=ModelProvider.OLLAMA, kind=ModelKind.LOCAL, models=OLLAMA_MODELS,
        endpoint="http://localhost:11434", status=ConnectionStatus.CONNECTED, is_primary=True,
        config={"activeModel": OLLAMA_MODELS[0]},
    ))

    agents: list[Agent] = []
    for name, role, skills in ROSTER:
        a = Agent(
            id=new_ulid(), org_id=DEMO_ORG, workspace_id=DEMO_WS, name=name, role_key=role,
            model_binding=OLLAMA_MODELS[0], models=list(OLLAMA_MODELS),
            status=AgentStatus.IDLE, skills=skills,
        )
        await store.add_agent(a)
        agents.append(a)

    # One team; the CTO is the Accountable decision-maker (final verdict), others advise.
    members = [{"agentId": a.id, "accountable": _val(a.role_key) == "cto"} for a in agents]
    team = Team(
        id=new_ulid(), org_id=DEMO_ORG, workspace_id=DEMO_WS, name="Core Team",
        description="The always-on team that staffs this project.", members=members,
        created_at=now, updated_at=now,
    )
    await store.add_team(team)

    key = await store.next_mission_key()
    await store.add_mission(Mission(
        id=new_ulid(), key=key, org_id=DEMO_ORG, workspace_id=DEMO_WS,
        title="TODO app simple version",
        summary="A simple TODO web app built with a local LLM.",
        requirements=(
            "Build a simple TODO web app (plain HTML/CSS/JS, no build step):\n"
            "- Add a task, mark it complete, delete it.\n"
            "- Persist tasks in localStorage.\n"
            "- A clean single-page UI with a title, input, and the task list."
        ),
        source=MissionSource.MANUAL, priority=Priority.P2, stage=MissionStage.BACKLOG,
        autonomy=AutonomyLevel.SUPERVISED, progress=0, project_kind="app", team_id=team.id,
        created_at=now, updated_at=now,
    ))

    for kind, name, cat in INTEGRATIONS:
        await store.add_integration(Integration(
            id=new_ulid(), org_id=DEMO_ORG, workspace_id=DEMO_WS,
            kind=kind, name=name, category=cat, status=ConnectionStatus.DISCONNECTED,
        ))

    print(f"Clean demo seeded: 1 Ollama connection, {len(agents)} agents, 1 team (Core Team), "
          f"mission {key} (TODO app simple version), {len(INTEGRATIONS)} integrations (disconnected).")
    ac = getattr(store, "aclose", None)
    if ac:
        await ac()


def _val(x: object) -> str:
    return str(getattr(x, "value", x)).lower()


if __name__ == "__main__":
    asyncio.run(main())
