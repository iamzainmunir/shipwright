"""End-to-end run-engine tests (in-process, deterministic scripted provider).

Covers the Phase-1 slice: start a run → it reaches the supervised merge gate (an approval
blocker) and suspends → resolve (approve) → resumes → mission shipped. Plus the reject path.
Driven at the engine layer for determinism (no HTTP, no Temporal, no network).
"""

from __future__ import annotations

import asyncio

import pytest
from app.engine import RunEngine
from app.events import EventBus
from app.store import InMemoryStore
from foundry_core.enums import ApprovalDecision, MissionStage, RunStatus, StepStatus

from tests.support.scripted_provider import ScriptedProvider


async def _wait_for(predicate, *, deadline: float = 3.0, interval: float = 0.01):
    elapsed = 0.0
    while elapsed < deadline:
        if await predicate():
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False


@pytest.fixture
def wiring() -> tuple[InMemoryStore, RunEngine]:
    store = InMemoryStore()
    # Clear seeded model connections so the run engine uses the injected deterministic provider
    # (the product no longer falls back to any fake provider — an unconnected model errors).
    store.model_connections.clear()
    engine = RunEngine(store, EventBus(), ScriptedProvider())
    return store, engine


async def test_run_reaches_gate_then_ships_on_approve(wiring):
    store, engine = wiring
    mission = await store.get_mission("FND-142")
    assert mission is not None

    run = await engine.start_run(mission)

    async def is_blocked() -> bool:
        r = await store.get_run(run.id)
        return r is not None and r.status == RunStatus.BLOCKED

    assert await _wait_for(is_blocked), "run should suspend at the merge gate"

    blockers = await store.list_blockers(mission.workspace_id, unresolved_only=True)
    assert len(blockers) == 1
    assert blockers[0].kind == "approval"

    m = await store.get_mission("FND-142")
    assert m is not None and m.is_blocked is True and m.stage == MissionStage.REVIEW

    await engine.resolve_blocker(blockers[0].id, ApprovalDecision.APPROVE, actor="u1", note="lgtm")

    async def is_shipped() -> bool:
        r = await store.get_run(run.id)
        return r is not None and r.status == RunStatus.SUCCEEDED

    assert await _wait_for(is_shipped), "run should complete after approval"

    m = await store.get_mission("FND-142")
    assert m is not None
    assert m.stage == MissionStage.SHIPPED
    assert m.progress == 100
    assert m.is_blocked is False

    steps = await store.list_steps(run.id)
    assert steps and all(s.status == StepStatus.DONE for s in steps)

    r = await store.get_run(run.id)
    assert r is not None and r.tokens_out > 0  # metering accumulated from the mock provider

    events = await store.list_events(run_id=run.id)
    # The deploy event is honest: it reports the pipeline finished and the merge was approved,
    # not the overclaim "shipped" (nothing is deployed to a real environment in this pipeline).
    assert any("pipeline complete" in e.text.lower() for e in events)


async def test_run_rejected_returns_to_review(wiring):
    store, engine = wiring
    mission = await store.get_mission("FND-142")
    assert mission is not None
    run = await engine.start_run(mission)

    assert await _wait_for(
        lambda: _status_is(store, run.id, RunStatus.BLOCKED)
    ), "run should suspend at the gate"

    blockers = await store.list_blockers(mission.workspace_id, unresolved_only=True)
    await engine.resolve_blocker(blockers[0].id, ApprovalDecision.REJECT, actor="u1", note="hold")

    assert await _wait_for(lambda: _status_is(store, run.id, RunStatus.FAILED))
    m = await store.get_mission("FND-142")
    assert m is not None and m.stage == MissionStage.REVIEW and m.is_blocked is False


async def _status_is(store: InMemoryStore, run_id: str, status: RunStatus) -> bool:
    r = await store.get_run(run_id)
    return r is not None and r.status == status
