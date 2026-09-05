"""Phase 3 — LangGraph engine (plan 02/03).

The strangler's final swap: the same decision-graph behavior as the legacy loop, but driven by a
checkpointed LangGraph StateGraph. These mirror the legacy end-to-end run-flow tests against
``GraphEngine`` to prove the graph path routes intake→…→review→ship, suspends at the merge gate,
and ships on approval / returns to review on reject — with all inherited invariants intact.
"""

from __future__ import annotations

import asyncio

import pytest
from app.engine import RunEngine
from app.events import EventBus
from app.graph_engine import GraphEngine
from app.store import InMemoryStore
from foundry_core.enums import ApprovalDecision, MissionStage, RunStatus, StepStatus

from tests.support.scripted_provider import ScriptedProvider


async def _wait_for(predicate, *, deadline: float = 5.0, interval: float = 0.01) -> bool:
    elapsed = 0.0
    while elapsed < deadline:
        if await predicate():
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False


@pytest.fixture
def wiring() -> tuple[InMemoryStore, GraphEngine]:
    store = InMemoryStore()
    store.model_connections.clear()
    engine = GraphEngine(store, EventBus(), ScriptedProvider())
    return store, engine


def test_graph_engine_is_a_run_engine():
    # Drop-in: same constructor + EngineProtocol surface, so state.get_engine can swap it in.
    store = InMemoryStore()
    engine = GraphEngine(store, EventBus(), ScriptedProvider())
    assert isinstance(engine, RunEngine)
    for method in ("start_run", "cancel_run", "retry_run", "request_change",
                   "resolve_blocker", "submit_clarification"):
        assert callable(getattr(engine, method))


async def test_graph_run_reaches_gate_then_ships_on_approve(wiring):
    store, engine = wiring
    mission = await store.get_mission("FND-142")
    assert mission is not None

    run = await engine.start_run(mission)

    async def is_blocked() -> bool:
        r = await store.get_run(run.id)
        return r is not None and r.status == RunStatus.BLOCKED

    assert await _wait_for(is_blocked), "graph run should suspend at the merge gate"

    blockers = await store.list_blockers(mission.workspace_id, unresolved_only=True)
    assert len(blockers) == 1 and blockers[0].kind == "approval"

    await engine.resolve_blocker(blockers[0].id, ApprovalDecision.APPROVE, actor="u1", note="lgtm")

    async def is_shipped() -> bool:
        r = await store.get_run(run.id)
        return r is not None and r.status == RunStatus.SUCCEEDED

    assert await _wait_for(is_shipped), "graph run should complete after approval"

    m = await store.get_mission("FND-142")
    assert m is not None and m.stage == MissionStage.SHIPPED and m.progress == 100
    assert m.is_blocked is False

    steps = await store.list_steps(run.id)
    assert steps and all(s.status == StepStatus.DONE for s in steps)
    events = await store.list_events(run_id=run.id)
    assert any("pipeline complete" in e.text.lower() for e in events)


async def test_graph_run_rejected_returns_to_review(wiring):
    store, engine = wiring
    mission = await store.get_mission("FND-142")
    assert mission is not None
    run = await engine.start_run(mission)

    assert await _wait_for(
        lambda: _status_is(store, run.id, RunStatus.BLOCKED)), "graph run should suspend at the gate"

    blockers = await store.list_blockers(mission.workspace_id, unresolved_only=True)
    await engine.resolve_blocker(blockers[0].id, ApprovalDecision.REJECT, actor="u1", note="hold")

    assert await _wait_for(lambda: _status_is(store, run.id, RunStatus.FAILED))
    m = await store.get_mission("FND-142")
    assert m is not None and m.stage == MissionStage.REVIEW and m.is_blocked is False


async def test_graph_run_creates_board_epic(wiring):
    # The inherited ticket seam still fires on the graph path (Epic created at start_run).
    store, engine = wiring
    mission = await store.get_mission("FND-142")
    await engine.start_run(mission)
    epics = [t for t in await store.list_tickets(mission_id=mission.id) if str(t.kind) == "epic"]
    assert len(epics) == 1


async def _status_is(store: InMemoryStore, run_id: str, status: RunStatus) -> bool:
    r = await store.get_run(run_id)
    return r is not None and r.status == status
