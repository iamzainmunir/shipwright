"""One-off: de-fake the already-seeded demo rows in an existing Postgres DB.

Fresh installs get honest seed data automatically (seed.py). This script brings a DB that was
seeded with the OLD fixtures up to the same honest state, WITHOUT deleting real runs:
  * agents        -> real per-role capability skills (were empty)
  * built-in skills -> uses = 0 (were 412 / 876 fabricated)
  * integrations  -> all disconnected (were hardcoded "connected")
  * FND-142       -> back to backlog / 0% / no branch, but ONLY if no run ever touched it

Run:  uv run python scripts/defake_existing.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.pgstore import PostgresStore  # noqa: E402

ROLE_SKILLS = {
    "pm": ["prd", "stories", "prioritization", "stakeholders"],
    "cto": ["architecture", "security-review", "tech-strategy", "scaling"],
    "backend": ["api-design", "tdd", "databases", "refactoring"],
    "qa": ["acceptance-tests", "regression", "browser-qa", "bug-triage"],
    "devops": ["ci-cd", "deploys", "observability", "rollback"],
    "frontend": ["ui", "components", "state", "accessibility"],
    "ba": ["requirements", "process-mapping", "acceptance-criteria"],
    "security": ["threat-modeling", "audit", "secrets", "compliance"],
    "designer": ["ux", "prototyping", "design-system", "research"],
}


def _val(x: object) -> str:
    return str(getattr(x, "value", x)).lower()


async def main() -> None:
    settings = get_settings()
    store = PostgresStore(settings.database_url, auto_migrate=False)
    await store.setup()

    n_agents = n_skills = n_integrations = n_missions = 0

    for agent in await store.list_agents():
        skills = ROLE_SKILLS.get(_val(agent.role_key))
        if skills and not agent.skills:
            await store.update_agent(agent.id, skills=skills)
            n_agents += 1

    for skill in await store.list_skills():
        if skill.uses and _val(skill.source) == "built-in":
            await store.update_skill(skill.id, uses=0)
            n_skills += 1

    for integ in await store.list_integrations():
        if _val(integ.status) != "disconnected":
            await store.set_integration_status(integ.kind, "disconnected")
            n_integrations += 1

    runs = await store.list_runs()
    for mission in await store.list_missions():
        if mission.key == "FND-142":
            has_run = any(r.mission_id == mission.id for r in runs)
            if not has_run and (mission.progress or mission.branch):
                await store.update_mission(mission.id, stage="backlog", progress=0, branch=None)
                n_missions += 1

    print(f"defake: agents+skills={n_agents}, skill.uses reset={n_skills}, "
          f"integrations disconnected={n_integrations}, missions reset={n_missions}")
    aclose = getattr(store, "aclose", None)
    if aclose:
        await aclose()


if __name__ == "__main__":
    asyncio.run(main())
