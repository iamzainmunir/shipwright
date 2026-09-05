"""Approving a merge gate after a restart must still finalize the mission (Phase 13 QA fix).

The in-process engine suspends a gate on an in-memory asyncio future; a process restart loses
it. `resolve_blocker` must then finalize the mission directly, so an approval is never a silent
no-op. (The durable Temporal engine handles this via signals.)
"""

from __future__ import annotations

from app.engine import RunEngine, _enum_value, _now
from app.events import EventBus
from app.store import InMemoryStore
from foundry_core.enums import (
    ApprovalDecision,
    BlockerKind,
    BlockerSeverity,
    MissionStage,
    RunStatus,
)
from foundry_core.ids import new_ulid
from foundry_core.models import Blocker, Run

from tests.support.scripted_provider import ScriptedProvider


async def _stuck_mission_with_gate(store: InMemoryStore) -> tuple[str, str]:
    """A mission suspended at a merge gate with NO pending future (simulates a restart)."""
    mission = (await store.list_missions())[0]
    await store.add_run(Run(
        id=new_ulid(), mission_id=mission.id, workspace_id=mission.workspace_id,
        status=RunStatus.BLOCKED, autonomy=mission.autonomy, started_at=_now(),
    ))
    blocker = Blocker(
        id=new_ulid(), org_id=mission.org_id, workspace_id=mission.workspace_id,
        mission_id=mission.id, kind=BlockerKind.APPROVAL, severity=BlockerSeverity.WARN,
        detail="Awaiting approval to merge & deploy.", created_at=_now(),
    )
    await store.add_blocker(blocker)
    await store.update_mission(mission.id, stage=MissionStage.REVIEW, is_blocked=True)
    return mission.id, blocker.id


async def test_approve_after_restart_ships() -> None:
    store = InMemoryStore()
    store.model_connections.clear()
    engine = RunEngine(store, EventBus(), ScriptedProvider())
    mission_id, blocker_id = await _stuck_mission_with_gate(store)

    await engine.resolve_blocker(blocker_id, ApprovalDecision.APPROVE, actor="qa", note=None)

    mission = await store.get_mission(mission_id)
    assert mission is not None
    assert mission.stage is MissionStage.SHIPPED
    assert mission.is_blocked is False


async def test_reject_after_restart_returns_to_review() -> None:
    store = InMemoryStore()
    store.model_connections.clear()
    engine = RunEngine(store, EventBus(), ScriptedProvider())
    mission_id, blocker_id = await _stuck_mission_with_gate(store)

    await engine.resolve_blocker(blocker_id, ApprovalDecision.REJECT, actor="qa", note=None)

    mission = await store.get_mission(mission_id)
    assert mission is not None
    assert mission.stage is MissionStage.REVIEW
    assert mission.is_blocked is False


async def test_approval_finalizes_gated_run_not_newest_run() -> None:
    """Regression: a merge approval must finalize the run parked at the gate, NOT merely the newest
    run. If a fresh re-run was started after the gate was raised, approving the (restart-orphaned)
    gate must still target the BLOCKED run — not the new one."""
    store = InMemoryStore()
    store.model_connections.clear()
    engine = RunEngine(store, EventBus(), ScriptedProvider())
    mission_id, blocker_id = await _stuck_mission_with_gate(store)
    gated_run = (await store.list_runs())[0]  # the blocked run created above

    # A newer run gets started AFTER the gate was raised (e.g. the user hit "Re-run build").
    newer = await store.add_run(Run(
        id=new_ulid(), mission_id=mission_id, workspace_id=gated_run.workspace_id,
        status=RunStatus.RUNNING, autonomy=gated_run.autonomy, started_at=_now(),
    ))

    await engine.resolve_blocker(blocker_id, ApprovalDecision.APPROVE, actor="qa", note=None)

    # The BLOCKED run is the one that shipped; the newer run must be left untouched (not shipped).
    assert _enum_value((await store.get_run(gated_run.id)).status) == "succeeded"
    assert _enum_value((await store.get_run(newer.id)).status) == "running"


async def test_start_run_supersedes_stale_gate() -> None:
    """Starting a fresh run supersedes a prior run parked at a gate and resolves its open blocker,
    so an old approval can never be mis-applied to the new run."""
    store = InMemoryStore()
    store.model_connections.clear()
    engine = RunEngine(store, EventBus(), ScriptedProvider())
    mission_id, blocker_id = await _stuck_mission_with_gate(store)
    old_run = (await store.list_runs())[0]
    mission = await store.get_mission(mission_id)
    assert mission is not None

    new_run = await engine.start_run(mission)

    assert _enum_value((await store.get_run(old_run.id)).status) == "cancelled"
    assert (await store.get_blocker(blocker_id)).resolved_at is not None
    assert new_run.id != old_run.id
    # Let the superseding run's background task settle without asserting on its (mock) outcome.
    for task in list(engine._tasks):
        task.cancel()
