"""Slack Socket Mode — request routing (slash commands + interactive) via a fake socket client.

The command/approval logic itself is covered by test_slack_integration.py; here we prove the socket
handler acks correctly and dispatches to the right resolver."""
from __future__ import annotations

from datetime import UTC, datetime

from app.slack_socket import _handle, _tokens
from app.store import InMemoryStore
from foundry_core.enums import BlockerKind, BlockerSeverity
from foundry_core.ids import new_ulid
from foundry_core.models import Blocker


class _FakeClient:
    def __init__(self) -> None:
        self.responses: list = []

    async def send_socket_mode_response(self, resp) -> None:
        self.responses.append(resp)


class _Req:
    def __init__(self, type_: str, payload: dict) -> None:
        self.type = type_
        self.envelope_id = "ENV1"
        self.payload = payload


async def test_handle_slash_command_status():
    store, client = InMemoryStore(), _FakeClient()
    await _handle(client, _Req("slash_commands", {"text": "status"}), store, engine=None)
    assert len(client.responses) == 1
    payload = client.responses[0].payload
    assert payload["response_type"] == "ephemeral"
    assert "Shipwright status" in payload["text"]


async def test_handle_slash_command_help_fallback():
    store, client = InMemoryStore(), _FakeClient()
    await _handle(client, _Req("slash_commands", {"text": "nonsense"}), store, engine=None)
    assert "commands" in client.responses[0].payload["text"].lower()


async def test_handle_interactive_acks_and_resolves():
    store = InMemoryStore()
    mission = await store.get_mission("FND-142")
    blocker = Blocker(
        id=new_ulid(), org_id=mission.org_id, workspace_id=mission.workspace_id,
        mission_id=mission.id, kind=BlockerKind.APPROVAL, severity=BlockerSeverity.WARN,
        detail="merge?", created_at=datetime.now(UTC),
    )
    await store.add_blocker(blocker)

    class _Engine:
        seen: tuple | None = None

        async def resolve_blocker(self, bid, decision, *, actor, note):
            _Engine.seen = (bid, actor)
            return blocker

    client = _FakeClient()
    # no response_url → the handler acks + resolves without any network call
    payload = {"actions": [{"value": f"approve:{blocker.id}"}], "user": {"username": "zain"}}
    await _handle(client, _Req("interactive", payload), store, _Engine())
    assert len(client.responses) == 1                      # acked
    assert _Engine.seen == (blocker.id, "slack:zain")      # resolved, attributed to the Slack user


async def test_handle_unknown_type_just_acks():
    store, client = InMemoryStore(), _FakeClient()
    await _handle(client, _Req("events_api", {}), store, engine=None)
    assert len(client.responses) == 1


async def test_tokens_empty_when_channel_off():
    store = InMemoryStore()
    # demo settings don't enable Slack two-way → no tokens → loop stays inert
    assert await _tokens(store) == ("", "")
