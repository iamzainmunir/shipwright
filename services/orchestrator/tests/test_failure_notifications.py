"""Every terminal failure — not only a human-halt — must notify the workspace's channels (email /
WhatsApp / Slack, incl. the WhatsApp Agent channel), so an escalation, provider error, or engine error
reaches the user instead of failing silently. The notification is funneled through ``_fail`` so no
failure path is left silent, and it fires exactly once (no double-send via ``_halt_for_user``)."""
from __future__ import annotations

from app.engine import RunEngine
from app.events import EventBus
from app.store import InMemoryStore
from foundry_core.enums import AgentRoleKey, RunStatus
from foundry_core.ids import new_ulid
from foundry_core.models import Run

from tests.support.scripted_provider import ScriptedProvider


class _SpyNotifier:
    """Records on_failed calls; no-ops the rest. Never raises (mirrors the real, failure-isolated one)."""

    def __init__(self) -> None:
        self.failed: list[tuple[str, str]] = []

    async def on_failed(self, mission, message: str = "") -> None:
        self.failed.append((mission.key, message))

    async def on_blocker(self, blocker, mission) -> None:
        pass

    async def on_completed(self, mission) -> None:
        pass


def _engine() -> tuple[InMemoryStore, RunEngine, _SpyNotifier]:
    store = InMemoryStore()
    store.model_connections.clear()  # so recall/reflect fall back to the injected provider
    engine = RunEngine(store, EventBus(), ScriptedProvider())
    spy = _SpyNotifier()
    engine._notifier = spy
    return store, engine, spy


async def _mission_and_run(store: InMemoryStore):
    mission = await store.get_mission("FND-142")  # seeded
    run = await store.add_run(Run(id=new_ulid(), mission_id=mission.id,
                                  workspace_id=mission.workspace_id, status=RunStatus.RUNNING))
    return mission, run


async def test_fail_notifies_on_every_failure_path():
    # A failure via _fail (escalation, provider error, engine error) must alert the channels.
    store, engine, spy = _engine()
    mission, run = await _mission_and_run(store)
    await engine._fail(run.id, mission.id, "openrouter auth failed (invalid or expired key)")
    assert spy.failed == [(mission.key, "openrouter auth failed (invalid or expired key)")]


async def test_halt_for_user_notifies_exactly_once():
    # _halt_for_user routes through _fail; the alert must fire ONCE, not twice.
    store, engine, spy = _engine()
    mission, run = await _mission_and_run(store)
    await engine._halt_for_user(run.id, mission.id, AgentRoleKey.CTO, "could not produce a shippable build")
    assert len(spy.failed) == 1
