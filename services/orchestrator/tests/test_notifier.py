"""Notifier gating + failure-isolation (offline; no real SMTP/HTTP).

Verifies the dispatch logic: master switch, per-event and per-channel gating, blocker-kind →
event mapping, and that one channel raising never propagates (the Observer rule).
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.notifier import Notifier
from app.store import InMemoryStore
from foundry_core.enums import BlockerKind, BlockerSeverity
from foundry_core.ids import new_ulid
from foundry_core.models import Blocker


def _blocker(mission, kind: BlockerKind) -> Blocker:
    return Blocker(
        id=new_ulid(), org_id=mission.org_id, workspace_id=mission.workspace_id,
        mission_id=mission.id, kind=kind, severity=BlockerSeverity.WARN,
        detail="needs attention", created_at=datetime.now(UTC),
    )


async def _setup(notify_enabled=True, channels=None, events=None):
    store = InMemoryStore()
    mission = await store.get_mission("FND-142")
    await store.update_settings(
        notify_enabled=notify_enabled,
        notify_channels=channels or {},
        notify_events=events or {},
    )
    return store, mission


async def test_disabled_dispatches_nothing():
    store, mission = await _setup(notify_enabled=False, channels={"slack": True}, events={"completed": True})
    fired: list[str] = []
    n = Notifier(store)
    n._send_slack = lambda *_a, **_k: _append(fired, "slack")  # type: ignore[assignment]
    await n.on_completed(mission)
    assert fired == []


async def test_event_and_channel_gating():
    store, mission = await _setup(
        channels={"slack": True, "email": False}, events={"completed": True, "blocker": False}
    )
    fired: list[str] = []
    n = Notifier(store)
    n._send_slack = lambda *_a, **_k: _append(fired, "slack")  # type: ignore[assignment]
    n._send_email = lambda *_a, **_k: _append(fired, "email")  # type: ignore[assignment]
    await n.on_completed(mission)          # completed on + slack on → fires slack only
    assert fired == ["slack"]
    fired.clear()
    await n.on_blocker(_blocker(mission, BlockerKind.QUESTION), mission)  # blocker event off → nothing
    assert fired == []


async def test_approval_vs_blocker_mapping():
    store, mission = await _setup(channels={"slack": True}, events={"approval": True, "blocker": False})
    fired: list[str] = []
    n = Notifier(store)
    n._send_slack = lambda *_a, **_k: _append(fired, "slack")  # type: ignore[assignment]
    await n.on_blocker(_blocker(mission, BlockerKind.APPROVAL), mission)   # approval on → fires
    assert fired == ["slack"]
    fired.clear()
    await n.on_blocker(_blocker(mission, BlockerKind.QUESTION), mission)   # blocker off → nothing
    assert fired == []


async def test_channel_failure_is_isolated():
    store, mission = await _setup(channels={"email": True, "slack": True}, events={"completed": True})
    fired: list[str] = []
    n = Notifier(store)

    async def boom(*_a, **_k):
        raise RuntimeError("smtp down")

    n._send_email = boom  # type: ignore[assignment]
    n._send_slack = lambda *_a, **_k: _append(fired, "slack")  # type: ignore[assignment]
    await n.on_completed(mission)  # email raises, but must not propagate; slack still fires
    assert fired == ["slack"]


async def _append(bucket: list[str], name: str) -> None:
    bucket.append(name)
