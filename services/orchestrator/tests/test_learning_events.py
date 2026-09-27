"""P7 — learning transparency: the engine emits compact events so the UI can show what knowledge is in
play (skills/memory recalled, or recall.empty), plus lesson.written / skill.learned."""
from __future__ import annotations

from app.engine import RunEngine
from app.events import EventBus
from app.store import InMemoryStore
from foundry_core.enums import AgentRoleKey, MemoryType, RunStatus, SkillCategory, SkillSource
from foundry_core.ids import new_ulid
from foundry_core.models import Memory, Run, Skill

from tests.support.scripted_provider import ScriptedProvider


def _engine():
    store = InMemoryStore()
    store.model_connections.clear()
    return store, RunEngine(store, EventBus(), ScriptedProvider())


async def _run_for(store, mission):
    return await store.add_run(Run(id=new_ulid(), mission_id=mission.id,
        workspace_id=mission.workspace_id, status=RunStatus.RUNNING, started_at="2026-01-01T00:00:00"))


async def _events(store, mission_id, kind):
    return [e for e in await store.list_events(mission_id=mission_id)
            if (e.payload or {}).get("kind") == kind]


async def test_emits_skill_and_memory_recalled():
    store, engine = _engine()
    mission = (await store.list_missions())[0]
    run = await _run_for(store, mission)
    skill = Skill(id=new_ulid(), workspace_id=mission.workspace_id, name="contract-first",
                  description="d", category=SkillCategory.ENGINEERING, source=SkillSource.LEARNED,
                  successes=3, fails=1, uses=7)
    mem = Memory(id=new_ulid(), workspace_id=mission.workspace_id, type=MemoryType.LESSON,
                 title="APIs use camelCase", body="b")
    await engine._emit_learning(run, mission, AgentRoleKey.BACKEND, [skill], [mem])
    sk = await _events(store, mission.id, "skill.recalled")
    mm = await _events(store, mission.id, "memory.recalled")
    assert sk and sk[0].payload["skills"][0]["name"] == "contract-first"
    assert sk[0].payload["skills"][0]["effectiveness"] == 0.75
    assert mm and mm[0].payload["memories"][0]["title"] == "APIs use camelCase"


async def test_emits_recall_empty_when_nothing():
    store, engine = _engine()
    mission = (await store.list_missions())[0]
    run = await _run_for(store, mission)
    await engine._emit_learning(run, mission, AgentRoleKey.QA, [], [])
    empty = await _events(store, mission.id, "recall.empty")
    assert empty, "an empty recall must be visible (A8), not silent"
