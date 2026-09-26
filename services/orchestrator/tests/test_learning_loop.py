"""Compounding-learning loop (audit A1/A8/A11/A12) — lessons on rework, reflection on failure,
recency-fallback recall, role-relevant skill recall with instructions, and the fixed auto-skill source.
"""
from __future__ import annotations

from app import reflection
from app.engine import RunEngine
from app.events import EventBus
from app.memory_search import rank_memories
from app.store import InMemoryStore
from foundry_core.enums import AgentRoleKey, MemoryType, SkillCategory, SkillSource
from foundry_core.ids import new_ulid
from foundry_core.models import Memory, Skill

from tests.support.scripted_provider import ScriptedProvider


def test_learned_skill_source_exists():
    # Regression: _persist_skill used SkillSource.PROJECT (nonexistent) so auto-skills NEVER saved.
    assert SkillSource.LEARNED.value == "learned"
    s = Skill(id=new_ulid(), workspace_id="w", name="x", description="d",
              category=SkillCategory.ENGINEERING, source=SkillSource.LEARNED)
    assert s.source == SkillSource.LEARNED


async def test_remember_lesson_writes_and_dedups():
    store = InMemoryStore()
    mission = (await store.list_missions())[0]
    before = len(await store.list_memories(mission.workspace_id))

    await reflection.remember_lesson(store, mission, "qa", "GET /api/stats 500s on empty inventory")
    mems = await store.list_memories(mission.workspace_id)
    lessons = [m for m in mems if str(getattr(m.type, "value", m.type)) == "lesson"]
    assert len(lessons) == 1
    assert lessons[0].embedding  # embedded so it can be recalled semantically
    assert "500s on empty inventory" in lessons[0].body

    # same cause again → no duplicate row
    await reflection.remember_lesson(store, mission, "qa", "GET /api/stats 500s on empty inventory")
    lessons2 = [m for m in await store.list_memories(mission.workspace_id)
                if str(getattr(m.type, "value", m.type)) == "lesson"]
    assert len(lessons2) == 1
    assert len(await store.list_memories(mission.workspace_id)) == before + 1


class _FakeProvider:
    def __init__(self, text: str) -> None:
        self._text = text

    async def complete(self, *, system, prompt, purpose="", max_tokens=256):
        from app.providers.base import LLMResult
        return LLMResult(text=self._text, model="fake", tokens_in=1, tokens_out=1, cost_cents=0)


async def test_reflect_writes_failure_memory():
    store = InMemoryStore()
    mission = (await store.list_missions())[0]
    await reflection.reflect(store, _FakeProvider("Next time, guard the empty-inventory aggregate."),
                             mission, "failed", "qa: /api/stats 500s")
    fails = [m for m in await store.list_memories(mission.workspace_id)
             if str(getattr(m.type, "value", m.type)) == "failure"]
    assert len(fails) == 1
    assert "Next time" in fails[0].body


async def test_reflect_none_writes_nothing():
    store = InMemoryStore()
    mission = (await store.list_missions())[0]
    before = len(await store.list_memories(mission.workspace_id))
    await reflection.reflect(store, _FakeProvider("NONE"), mission, "failed", "ctx")
    assert len(await store.list_memories(mission.workspace_id)) == before


def test_rank_memories_recency_fallback_on_zero_overlap():
    # A8: a query with no lexical overlap used to return [] — now it falls back to recent memories.
    from datetime import UTC, datetime
    old = Memory(id="1", workspace_id="w", type=MemoryType.LESSON, title="old", body="alpha",
                 updated_at=datetime(2026, 1, 1, tzinfo=UTC))
    new = Memory(id="2", workspace_id="w", type=MemoryType.LESSON, title="new", body="beta",
                 updated_at=datetime(2026, 9, 1, tzinfo=UTC))
    out = rank_memories([old, new], "zzzznomatch qqqq", k=1)
    assert out and out[0].id == "2"  # most-recent surfaced, not empty


async def test_recall_skills_surfaces_instructions_and_filters_role():
    store = InMemoryStore()
    engine = RunEngine(store, EventBus(), ScriptedProvider())
    mission = (await store.list_missions())[0]
    await store.add_skill(Skill(
        id=new_ulid(), workspace_id=mission.workspace_id, name="contract-first-api",
        description="define the API contract before building",
        category=SkillCategory.ENGINEERING, source=SkillSource.LEARNED,
        instructions="Write the OpenAPI shape, then implement against it.",
        auto_invoke=True, installed=True,
    ))
    text, picked = await engine._recall_skills(mission, AgentRoleKey.BACKEND)
    assert "contract-first-api" in text
    assert "How: Write the OpenAPI shape" in text        # instructions surfaced (A5)
    assert any(s.name == "contract-first-api" for s in picked)

    # a design-role recall must NOT get the engineering skill (no arbitrary skills[:2] fallback, A6)
    text_d, picked_d = await engine._recall_skills(mission, AgentRoleKey.DESIGNER)
    assert "contract-first-api" not in text_d
