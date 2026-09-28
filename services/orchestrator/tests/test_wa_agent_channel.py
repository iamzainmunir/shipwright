"""P5 — WhatsApp Agent Platform channel: long-poll receive → P4 brain → reply → persist offset,
inert until configured, with the manual's send guards. All against a mocked HTTP client (no network)."""
from __future__ import annotations

from app import wa_agent_channel as ch
from app.engine import RunEngine
from app.events import EventBus
from app.store import InMemoryStore

from tests.support.scripted_provider import ScriptedProvider


class _Resp:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data


class _FakeClient:
    """Records GET/POST calls and returns queued responses."""

    def __init__(self, get_resp=None, post_resp=None):
        self._get_resp = get_resp or _Resp(200, {})
        self._post_resp = post_resp or _Resp(200, {"messages": [{"id": "wamid.1"}]})
        self.gets: list = []
        self.posts: list = []

    async def get(self, url, params=None, headers=None):
        self.gets.append({"url": url, "params": params, "headers": headers})
        return self._get_resp

    async def post(self, url, json=None, headers=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return self._post_resp


def _engine():
    store = InMemoryStore()
    store.model_connections.clear()
    return store, RunEngine(store, EventBus(), ScriptedProvider())


async def _configure(store, key="secret-agent-token"):
    await store.update_settings(notify_enabled=True,
                                notify_channels={"whatsapp_agent": True},
                                notify_config={"whatsappAgentKey": key})


def test_text_messages_flatten():
    updates = {"entry": [{"changes": [
        {"field": "messages", "value": {"messages": [{"from": "user:1", "text": {"body": "hi"}}]}},
        {"field": "statuses", "value": {"statuses": [{"id": "x"}]}},
    ]}]}
    msgs = ch._text_messages(updates)
    assert len(msgs) == 1 and msgs[0]["from"] == "user:1"


async def test_poll_once_inert_without_token():
    store, engine = _engine()  # notify not enabled / no key
    client = _FakeClient()
    assert await ch.poll_once(store, engine, client=client) is None
    assert client.gets == [] and client.posts == []  # inert: no network at all


async def test_poll_once_routes_replies_and_persists_offset():
    store, engine = _engine()
    await _configure(store)
    updates = {"entry": [{"changes": [{"field": "messages", "value": {"messages": [
        {"from": "user:abc", "id": "wamid.in1", "type": "text", "text": {"body": "status"}}]}}]}],
        "next_offset": "off-2"}
    client = _FakeClient(get_resp=_Resp(200, updates))
    new_offset = await ch.poll_once(store, engine, client=client)
    assert new_offset == "off-2"
    # a reply was sent to the creator's user:<id>, with a text body
    assert client.posts and client.posts[0]["json"]["to"] == "user:abc"
    assert client.posts[0]["json"]["type"] == "text" and client.posts[0]["json"]["text"]["body"]
    # offset persisted for restart-safe resume
    prefs = await store.get_settings()
    assert prefs.notify_config["whatsappAgentOffset"] == "off-2"


async def test_poll_once_captures_recipient_for_proactive_push():
    # The Agent channel learns WHERE to reach the creator from their inbound message, so the notifier
    # can later push blockers/approvals/questions to that same user:<id>.
    store, engine = _engine()
    await _configure(store)
    updates = {"entry": [{"changes": [{"field": "messages", "value": {"messages": [
        {"from": "user:abc", "id": "wamid.in1", "type": "text", "text": {"body": "status"}}]}}]}],
        "next_offset": "off-2"}
    client = _FakeClient(get_resp=_Resp(200, updates))
    await ch.poll_once(store, engine, client=client)
    prefs = await store.get_settings()
    assert prefs.notify_config["whatsappAgentRecipient"] == "user:abc"


async def test_poll_once_204_is_clean_empty():
    # 204 No Content = long-poll timed out with no new messages — a no-op, not an error.
    store, engine = _engine()
    await _configure(store)
    client = _FakeClient(get_resp=_Resp(204, {}))
    assert await ch.poll_once(store, engine, client=client) is None
    assert client.posts == []  # nothing to reply to


async def test_poll_once_cold_start_omits_offset():
    store, engine = _engine()
    await _configure(store)
    client = _FakeClient(get_resp=_Resp(200, {}))  # empty → timeout no-op
    await ch.poll_once(store, engine, client=client)
    # first poll has no stored offset → omit it (skip 30-day backlog)
    assert "offset" not in (client.gets[0]["params"] or {})


async def test_non_text_message_gets_polite_reply():
    store, engine = _engine()
    await _configure(store)
    updates = {"entry": [{"changes": [{"field": "messages", "value": {"messages": [
        {"from": "user:abc", "id": "wamid.img", "type": "image", "image": {"id": "m1"}}]}}]}],
        "next_offset": "off-3"}
    client = _FakeClient(get_resp=_Resp(200, updates))
    await ch.poll_once(store, engine, client=client)
    assert client.posts and "text" in client.posts[0]["json"]["text"]["body"].lower()


async def test_send_text_guards_and_truncates():
    cfg = {"whatsappAgentKey": "k"}
    # bad `to` (must be user:<id>) → refused, no send
    client = _FakeClient()
    assert await ch.send_text(cfg, "agent:1", "hi", client=client) is None
    assert client.posts == []
    # good send returns the message id and truncates to 4096
    client2 = _FakeClient(post_resp=_Resp(200, {"messages": [{"id": "wamid.9"}]}))
    mid = await ch.send_text(cfg, "user:1", "x" * 5000, client=client2)
    assert mid == "wamid.9"
    assert len(client2.posts[0]["json"]["text"]["body"]) == 4096
