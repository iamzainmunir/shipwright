"""WhatsApp two-way integration — signature verification (security) + inbound message handling."""
from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime

from app.store import InMemoryStore
from app.whatsapp_integration import (
    handle_message,
    number_allowed,
    parse_numbers,
    verify_meta,
    verify_twilio,
)
from foundry_core.enums import BlockerKind, BlockerSeverity
from foundry_core.ids import new_ulid
from foundry_core.models import Blocker


def test_verify_twilio_accepts_valid_and_rejects_bad():
    token, url = "authtok", "https://x.test/api/v1/integrations/whatsapp/twilio"
    params = {"Body": "status", "From": "whatsapp:+1555"}
    base = url + "".join(k + params[k] for k in sorted(params))
    sig = base64.b64encode(hmac.new(token.encode(), base.encode(), hashlib.sha1).digest()).decode()
    assert verify_twilio(token, url, params, sig) is True
    assert verify_twilio(token, url, {**params, "Body": "x"}, sig) is False  # tampered params
    assert verify_twilio("wrong", url, params, sig) is False                 # wrong token
    assert verify_twilio("", url, params, sig) is False                      # not configured


def test_number_allowed_matches_by_digits():
    allowed = parse_numbers("+1 415 555 1234, +1 415 555 9999")
    assert number_allowed("whatsapp:+14155551234", allowed) is True   # Twilio format
    assert number_allowed("14155551234", allowed) is True             # Meta format (digits)
    assert number_allowed("whatsapp:+14155550000", allowed) is False  # not on the list
    assert number_allowed("whatsapp:+14155551234", []) is False       # empty list → deny all
    assert number_allowed("", allowed) is False                       # no sender → deny


def test_verify_meta_accepts_valid_and_rejects_bad():
    secret, body = "appsecret", b'{"entry":[]}'
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_meta(secret, body, sig) is True
    assert verify_meta(secret, body + b"x", sig) is False   # tampered body
    assert verify_meta("wrong", body, sig) is False         # wrong secret
    assert verify_meta("", body, sig) is False              # not configured


async def test_handle_message_status_query():
    store = InMemoryStore()  # seeded demo missions
    reply = await handle_message("status", store, engine=None)
    assert "Shipwright status" in reply
    assert (await handle_message("", store, engine=None)).startswith("Send")  # empty → usage hint


async def test_handle_message_approve_by_mission_key():
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

    reply = await handle_message("approve FND-142", store, _Engine())
    assert "Approved" in reply
    assert _Engine.seen[0] == blocker.id
    assert _Engine.seen[2] == "whatsapp"


async def test_handle_message_approve_unknown_mission():
    store = InMemoryStore()
    reply = await handle_message("approve NOPE-999", store, engine=None)
    assert "not found" in reply


async def test_handle_message_approve_without_pending_gate():
    store = InMemoryStore()
    reply = await handle_message("reject FND-142", store, engine=None)
    assert "No pending approval" in reply
