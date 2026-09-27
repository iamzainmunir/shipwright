"""A14 — skill effectiveness metrics drive recall ranking + retirement, and terminal outcomes
credit/debit the skills a run recalled."""
from __future__ import annotations

from app.engine import (
    RunEngine,
    _skill_effectiveness,
    _skill_should_demote,
)
from app.events import EventBus
from app.store import InMemoryStore
from foundry_core.enums import AgentRoleKey, SkillCategory, SkillSource
from foundry_core.ids import new_ulid
from foundry_core.models import Skill

from tests.support.scripted_provider import ScriptedProvider


def _sk(name, successes=0, fails=0, **kw):
    return Skill(id=new_ulid(), workspace_id="__demo_ws__", name=name, description=f"{name} skill",
                 category=SkillCategory.ENGINEERING, source=SkillSource.LEARNED,
                 auto_invoke=True, installed=True, successes=successes, fails=fails, **kw)


def test_effectiveness_and_demote_pure():
    assert _skill_effectiveness(_sk("x")) == 0.5              # neutral prior with no history
    assert _skill_effectiveness(_sk("x", successes=3, fails=1)) == 0.75
    assert _skill_should_demote(_sk("bad", successes=0, fails=5)) is True
    assert _skill_should_demote(_sk("young", successes=0, fails=2)) is False  # not enough samples
    assert _skill_should_demote(_sk("good", successes=4, fails=1)) is False


def _engine():
    store = InMemoryStore()
    store.model_connections.clear()
    return store, RunEngine(store, EventBus(), ScriptedProvider())


async def test_recall_ranks_by_effectiveness_and_records_recalled():
    store, engine = _engine()
    mission = (await store.list_missions())[0]
    ws = mission.workspace_id
    await store.add_skill(_sk("low-value", successes=0, fails=3).model_copy(update={"workspace_id": ws}))
    await store.add_skill(_sk("high-value", successes=5, fails=0).model_copy(update={"workspace_id": ws}))
    text, picked = await engine._recall_skills(mission, AgentRoleKey.BACKEND)
    # high-value ranks first in the injected block
    assert picked[0].name == "high-value"
    assert "high-value" in text
    # recalled skills are tracked for outcome scoring
    assert engine._recalled_skills[mission.id] == {p.id for p in picked}


async def test_proven_bad_skill_is_auto_demoted():
    store, engine = _engine()
    mission = (await store.list_missions())[0]
    ws = mission.workspace_id
    bad = _sk("chronically-bad", successes=0, fails=5).model_copy(update={"workspace_id": ws})
    await store.add_skill(bad)
    await engine._recall_skills(mission, AgentRoleKey.BACKEND)
    refreshed = next(s for s in await store.list_skills(ws) if s.id == bad.id)
    assert refreshed.auto_invoke is False  # retired from auto-invoke


async def test_record_skill_outcome_credits_and_debits():
    store, engine = _engine()
    mission = (await store.list_missions())[0]
    ws = mission.workspace_id
    s = _sk("winner", successes=1, fails=0).model_copy(update={"workspace_id": ws})
    await store.add_skill(s)
    # simulate a run that recalled it, then shipped
    engine._recalled_skills[mission.id] = {s.id}
    await engine._record_skill_outcome(mission.id, success=True)
    after = next(x for x in await store.list_skills(ws) if x.id == s.id)
    assert after.successes == 2 and after.fails == 0
    assert mission.id not in engine._recalled_skills  # consumed

    # and a run that failed debits
    engine._recalled_skills[mission.id] = {s.id}
    await engine._record_skill_outcome(mission.id, success=False)
    after2 = next(x for x in await store.list_skills(ws) if x.id == s.id)
    assert after2.fails == 1
