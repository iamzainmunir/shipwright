"""Slack two-way integration — signature verification (security), status resolver, approve action."""
from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime

from app.slack_integration import approval_blocks, resolve_action, resolve_command, verify_slack
from app.store import InMemoryStore
from foundry_core.enums import BlockerKind, BlockerSeverity
from foundry_core.ids import new_ulid
from foundry_core.models import Blocker


def _sign(secret: str, ts: str, body: str) -> str:
    return "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()


def test_verify_slack_accepts_valid_and_rejects_bad():
    secret, ts, body = "s3cr3t", str(int(time.time())), "command=/shipwright&text=status"
    sig = _sign(secret, ts, body)
    assert verify_slack(secret, ts, sig, body.encode()) is True
    assert verify_slack(secret, ts, sig, (body + "x").encode()) is False   # tampered body
    assert verify_slack("wrong", ts, sig, body.encode()) is False          # wrong secret
    assert verify_slack("", ts, sig, body.encode()) is False               # no secret configured


def test_verify_slack_rejects_stale_timestamp():
    secret, body = "s3cr3t", "text=status"
    old = str(int(time.time()) - 999)  # older than the 5-min window → replay-rejected
    assert verify_slack(secret, old, _sign(secret, old, body), body.encode()) is False


async def test_resolve_command_status_missions_help():
    store = InMemoryStore()  # seeded demo missions
    status = await resolve_command("status", store)
    assert status["response_type"] == "ephemeral"
    assert "Shipwright status" in status["text"]
    assert "missions" in (await resolve_command("missions", store))["text"].lower()
    assert "commands" in (await resolve_command("nonsense", store))["text"].lower()  # help fallback


async def test_resolve_action_approves_blocker():
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
            _Engine.seen = (bid, str(decision), actor)
            return blocker

    payload = {"user": {"username": "zain"}, "actions": [{"value": f"approve:{blocker.id}"}]}
    r = await resolve_action(payload, store, _Engine())
    assert "Approved" in r["text"]
    assert _Engine.seen[0] == blocker.id
    assert _Engine.seen[2] == "slack:zain"


def test_approval_blocks_carry_blocker_id():
    blocks = approval_blocks("Approve?", "gate detail", "BLK1")
    buttons = blocks[1]["elements"]
    assert buttons[0]["value"] == "approve:BLK1"
    assert buttons[1]["value"] == "reject:BLK1"
