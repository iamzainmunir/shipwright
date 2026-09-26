"""A2 — fail-closed auto-ship gate.

The audit's CRITICAL finding: with the sandbox off (the default), a build produces no ground-truth
facts, so `_build_is_healthy` fell back to *healthy* and an AUTONOMOUS mission auto-approved the merge
on the LLM's QA verdict alone. These tests pin the fix: an autonomous merge auto-approves ONLY when the
work was genuinely VERIFIED this session (real build / on-disk deliverable / runtime QA evidence);
an unverified build never ships without a human — it halts at the gate instead.

A *supervised* mission always hits the human gate regardless, so it is unaffected (and shipping via an
explicit human approval still works).
"""
from __future__ import annotations

import asyncio

from app.engine import RunEngine
from app.events import EventBus
from app.store import InMemoryStore
from foundry_core.enums import (
    ApprovalDecision,
    AutonomyLevel,
    RunStatus,
    StepStatus,
)
from foundry_core.ids import new_ulid
from foundry_core.models import Run, Step

from tests.support.scripted_provider import ScriptedProvider


def _engine() -> tuple[InMemoryStore, RunEngine]:
    store = InMemoryStore()
    store.model_connections.clear()
    return store, RunEngine(store, EventBus(), ScriptedProvider())


async def _drive(store: InMemoryStore, run_id: str, *, tries: int = 400) -> str:
    for _ in range(tries):
        r = await store.get_run(run_id)
        if r and r.status in (RunStatus.BLOCKED, RunStatus.SUCCEEDED, RunStatus.FAILED):
            return r.status.value if hasattr(r.status, "value") else str(r.status)
        await asyncio.sleep(0.02)
    raise AssertionError("run did not reach a terminal/gate state in time")


async def _wait(store: InMemoryStore, run_id: str, status: RunStatus, *, tries: int = 400) -> None:
    for _ in range(tries):
        r = await store.get_run(run_id)
        if r and r.status == status:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"run did not reach {status} in time")


def test_is_verified_false_until_real_evidence() -> None:
    _store, engine = _engine()
    mid = "M_TEST"
    assert engine._is_verified(mid) is False        # absence of evidence is never "verified"
    engine._verified[mid] = True
    assert engine._is_verified(mid) is True


async def _seed_run_at_ship(store: InMemoryStore, *, autonomous: bool) -> tuple[str, str, str]:
    mission = (await store.list_missions())[0]
    await store.update_mission(
        mission.id,
        autonomy=AutonomyLevel.AUTONOMOUS if autonomous else AutonomyLevel.SUPERVISED,
    )
    run = await store.add_run(Run(
        id=new_ulid(), mission_id=mission.id, workspace_id=mission.workspace_id,
        status=RunStatus.RUNNING, autonomy=mission.autonomy, started_at="2026-01-01T00:00:00"))
    step = await store.add_step(Step(
        id=new_ulid(), run_id=run.id, phase="ship", title="ship",
        agent_role="devops", status=StepStatus.ACTIVE, started_at="2026-01-01T00:00:00"))
    return run.id, mission.id, step.id


async def test_autonomous_verified_build_auto_ships() -> None:
    # A healthy AND verified build in autonomous mode auto-approves the merge with no human (dry-run
    # ship because no repo is connected → completes locally).
    store, engine = _engine()
    run_id, mid, step_id = await _seed_run_at_ship(store, autonomous=True)
    engine._build_facts[mid] = {"healthy": True, "files": ["src/app.py"], "steps": 3}
    engine._verified[mid] = True

    proceeded = await asyncio.wait_for(engine._ship_gate(run_id, mid, step_id), timeout=5)
    assert proceeded is True                                   # auto-approved + shipped, no blocker
    open_blockers = [b for b in await store.list_blockers()
                     if b.mission_id == mid and not b.resolved_at]
    assert not open_blockers, "a verified autonomous ship must not stop for a human"


async def test_autonomous_unverified_build_halts_for_human_never_auto_ships() -> None:
    # THE hole: healthy-by-absence but never verified. Autonomous must NOT auto-ship — it opens the
    # approval gate and waits for a human. Only an explicit approval then completes the ship.
    store, engine = _engine()
    run_id, mid, step_id = await _seed_run_at_ship(store, autonomous=True)
    engine._build_facts[mid] = {"healthy": True, "files": ["src/app.py"], "steps": 3}
    # deliberately NO engine._verified[mid] → unverified

    gate = asyncio.create_task(engine._ship_gate(run_id, mid, step_id))
    open_blockers: list = []
    for _ in range(150):
        await asyncio.sleep(0.02)
        open_blockers = [b for b in await store.list_blockers()
                         if b.mission_id == mid and not b.resolved_at]
        if open_blockers:
            break
    assert not gate.done(), "autonomous unverified build auto-shipped — the A2 hole is open"
    assert open_blockers, "expected an approval gate for the unverified build"

    await engine.resolve_blocker(open_blockers[0].id, ApprovalDecision.APPROVE, actor="owner", note=None)
    proceeded = await asyncio.wait_for(gate, timeout=5)
    assert proceeded is True


async def test_sim_mode_never_fabricates_verification_supervised_still_ships() -> None:
    # A full sim-mode (sandbox off) run produces NO ground-truth evidence, so the mission must reach the
    # ship gate UNVERIFIED (never a false-positive verification). A supervised mission still ships once a
    # human approves — proving the fail-closed gate doesn't break the normal human-gated flow.
    store, engine = _engine()
    engine.sandbox_enabled = False  # force the LLM-only build path regardless of the ambient .env
    mission = (await store.list_missions())[0]
    run = await engine.start_run(mission)
    assert await _drive(store, run.id) == "blocked"           # parked at the human ship gate
    assert engine._is_verified(mission.id) is False, "sim mode must not fabricate verification"

    blk = [b for b in await store.list_blockers()
           if b.mission_id == mission.id and not b.resolved_at]
    await engine.resolve_blocker(blk[0].id, ApprovalDecision.APPROVE, actor="owner", note=None)
    await _wait(store, run.id, RunStatus.SUCCEEDED)           # human approval still ships it
