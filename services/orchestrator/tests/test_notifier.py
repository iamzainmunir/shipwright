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


async def test_wa_agent_fires_when_channel_on_and_recipient_captured():
    store, mission = await _setup(channels={"whatsapp_agent": True}, events={"completed": True})
    await store.update_settings(notify_config={"whatsappAgentKey": "k", "whatsappAgentRecipient": "user:abc"})
    fired: list[str] = []
    n = Notifier(store)
    n._send_wa_agent = lambda *_a, **_k: _append(fired, "wa_agent")  # type: ignore[assignment]
    await n.on_completed(mission)
    assert fired == ["wa_agent"]


async def test_wa_agent_ready_requires_key_and_recipient():
    store = InMemoryStore()
    await store.update_settings(notify_config={"whatsappAgentKey": "k"})  # key but no recipient yet
    prefs = await store.get_settings()
    assert Notifier._channel_ready(prefs, "whatsapp_agent") is False
    await store.update_settings(notify_config={"whatsappAgentKey": "k", "whatsappAgentRecipient": "user:1"})
    prefs = await store.get_settings()
    assert Notifier._channel_ready(prefs, "whatsapp_agent") is True


async def test_send_wa_agent_pushes_to_captured_recipient(monkeypatch):
    store = InMemoryStore()
    await store.update_settings(notify_config={"whatsappAgentKey": "k", "whatsappAgentRecipient": "user:zed"})
    prefs = await store.get_settings()
    sent: dict = {}

    async def fake_send_text(cfg, to, body, **_kw):
        sent.update(to=to, body=body, key=cfg.get("whatsappAgentKey"))
        return "wamid.x"

    monkeypatch.setattr("app.wa_agent_channel.send_text", fake_send_text)
    await Notifier(store)._send_wa_agent(prefs, "Subject line", "Body line")
    assert sent["to"] == "user:zed" and sent["key"] == "k"
    assert "Subject line" in sent["body"] and "Body line" in sent["body"]


async def test_send_wa_agent_skips_without_recipient(monkeypatch):
    store = InMemoryStore()
    await store.update_settings(notify_config={"whatsappAgentKey": "k"})  # no recipient captured
    prefs = await store.get_settings()
    called = False

    async def fake_send_text(*_a, **_k):
        nonlocal called
        called = True
        return "x"

    monkeypatch.setattr("app.wa_agent_channel.send_text", fake_send_text)
    await Notifier(store)._send_wa_agent(prefs, "s", "b")
    assert called is False  # nothing to push to → no network


async def test_question_arms_agent_recipient_session():
    # A question pushed over the Agent channel must be answerable by replying in the Agent chat:
    # arm the session under the same key wa_inbound uses (normalize_sender of the user:<id>).
    store, mission = await _setup(channels={"whatsapp_agent": True}, events={"question": True})
    await store.update_settings(notify_config={"whatsappAgentKey": "k", "whatsappAgentRecipient": "user:12345"})
    n = Notifier(store)

    async def _noop(*_a, **_k):
        return None

    n._send_wa_agent = _noop  # type: ignore[assignment] — swallow the outbound send
    await n.on_blocker(_blocker(mission, BlockerKind.QUESTION), mission)
    from app.whatsapp_integration import normalize_sender
    sess = await store.get_wa_session(mission.workspace_id, normalize_sender("user:12345"))
    assert sess is not None and sess.state == "awaiting_answer"
    assert sess.context["mission_key"] == mission.key


async def test_push_reject_gate_arms_push_decision_session():
    # A push-rejected re-gate must arm the Agent recipient's session so a reply ('force push' /
    # 'new branch <name>') routes to that gate — not the start-mission parser.
    store, mission = await _setup(channels={"whatsapp_agent": True}, events={"approval": True})
    await store.update_settings(notify_config={"whatsappAgentKey": "k", "whatsappAgentRecipient": "user:12345"})
    n = Notifier(store)

    async def _noop(*_a, **_k):
        return None

    n._send_wa_agent = _noop  # type: ignore[assignment]
    blocker = Blocker(
        id=new_ulid(), org_id=mission.org_id, workspace_id=mission.workspace_id, mission_id=mission.id,
        kind=BlockerKind.APPROVAL, severity=BlockerSeverity.WARN,
        detail=("The push was rejected. Authorize a force-push to overwrite that branch, or pick a "
                "different branch, then approve again."),
        created_at=datetime.now(UTC),
    )
    await n.on_blocker(blocker, mission)
    from app.whatsapp_integration import normalize_sender
    sess = await store.get_wa_session(mission.workspace_id, normalize_sender("user:12345"))
    assert sess is not None and sess.state == "awaiting_push_decision"
    assert sess.context["blocker_id"] == blocker.id and sess.context["mission_key"] == mission.key


async def _append(bucket: list[str], name: str) -> None:
    bucket.append(name)
