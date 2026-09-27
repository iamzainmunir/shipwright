"""P4 — the stateful WhatsApp inbound bridge: sessions persist across messages, destructive actions
require confirm, an armed session routes a plain reply to the mission's clarification, and provider
sender formats (Twilio 'whatsapp:+1…' vs Meta '1…') map to the SAME session."""
from __future__ import annotations

from app import wa_inbound
from app.engine import RunEngine
from app.events import EventBus
from app.store import InMemoryStore
from foundry_core.enums import BlockerKind, BlockerSeverity, RunStatus
from foundry_core.ids import new_ulid
from foundry_core.models import Blocker, Run

from tests.support.scripted_provider import ScriptedProvider


def _engine():
    store = InMemoryStore()
    store.model_connections.clear()
    return store, RunEngine(store, EventBus(), ScriptedProvider())


async def test_start_requires_confirm_then_creates_mission():
    store, engine = _engine()
    before = len(await store.list_missions())
    # first message: start with a brief → must NOT create a mission yet, asks to confirm
    r1 = await wa_inbound.handle_stateful("start build a todo app", "whatsapp:+15551234", store, engine)
    assert "confirm" in r1.lower()
    assert len(await store.list_missions()) == before, "must not start before confirmation"
    # second message: confirm → creates + starts a mission
    r2 = await wa_inbound.handle_stateful("confirm", "whatsapp:+15551234", store, engine)
    assert "started" in r2.lower() or "✅" in r2
    assert len(await store.list_missions()) == before + 1


async def test_session_key_is_provider_independent():
    store, engine = _engine()
    # arm via Twilio-style sender…
    await wa_inbound.handle_stateful("start a thing", "whatsapp:+1 555 000 1111", store, engine)
    # …a Meta-style sender with the same digits sees the SAME session (awaiting confirm)
    r = await wa_inbound.handle_stateful("confirm", "15550001111", store, engine)
    assert "started" in r.lower() or "✅" in r


async def test_status_query_is_readonly():
    store, engine = _engine()
    before = len(await store.list_missions())
    r = await wa_inbound.handle_stateful("status", "whatsapp:+15551234", store, engine)
    assert isinstance(r, str) and r  # a status reply, no state change / no mission created
    assert len(await store.list_missions()) == before  # a read-only query creates nothing


async def test_armed_session_routes_answer_to_clarification():
    store, engine = _engine()
    mission = (await store.list_missions())[0]
    run = await store.add_run(Run(id=new_ulid(), mission_id=mission.id,
        workspace_id=mission.workspace_id, status=RunStatus.RUNNING, started_at="2026-01-01T00:00:00"))
    blk = await store.add_blocker(Blocker(
        id=new_ulid(), run_id=run.id, mission_id=mission.id, kind=BlockerKind.QUESTION,
        severity=BlockerSeverity.WARN, detail='{"questions": ["Which DB?"]}'))

    # arm the sender's session (as the notifier would) via the store directly
    from datetime import UTC, datetime, timedelta

    from app.whatsapp_integration import normalize_sender
    from foundry_core.models import WaSession
    key = normalize_sender("whatsapp:+15559999")
    await store.upsert_wa_session(WaSession(
        id="WS1", workspace_id=mission.workspace_id, sender=key, state="awaiting_answer",
        context={"mission_key": mission.key, "blocker_id": blk.id, "questions": ["Which DB?"]},
        updated_at=datetime.now(UTC), expires_at=datetime.now(UTC) + timedelta(hours=1)))

    # record whether submit_clarification is called
    called = {}
    orig = engine.submit_clarification

    async def _spy(blocker_id, answers, *, actor=None):
        called["blocker_id"] = blocker_id
        called["answers"] = answers
        return await orig(blocker_id, answers, actor=actor)
    engine.submit_clarification = _spy  # type: ignore[method-assign]

    reply = await wa_inbound.handle_stateful("Postgres please", "whatsapp:+15559999", store, engine)
    assert called.get("blocker_id") == blk.id, "an armed session must route the reply to the clarification"
    assert isinstance(reply, str) and reply


async def test_handle_stateful_never_raises_on_garbage():
    store, engine = _engine()
    r = await wa_inbound.handle_stateful("\x00\x01 garbage ????", "whatsapp:+15551234", store, engine)
    assert isinstance(r, str)  # a safe reply, never an exception
