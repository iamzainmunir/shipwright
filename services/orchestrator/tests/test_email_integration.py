"""Email two-way — reply parsing + the sender-allow-list trust boundary."""
from __future__ import annotations

from datetime import UTC, datetime

from app.email_integration import (
    email_auth_ok,
    extract_mission_key,
    first_command_line,
    handle_email,
    parse_allowed,
    sender_allowed,
    synthesize_command,
)
from app.store import InMemoryStore
from foundry_core.enums import BlockerKind, BlockerSeverity
from foundry_core.ids import new_ulid
from foundry_core.models import Blocker

# A realistic Gmail-style Authentication-Results header for a legit (DMARC-passing) reply.
_AUTH_PASS = "mx.google.com; dkim=pass header.d=gmail.com; spf=pass; dmarc=pass"


def test_extract_mission_key():
    assert extract_mission_key("Re: [Shipwright] Approval needed — SW-142: ship it") == "SW-142"
    assert extract_mission_key("Re: [Shipwright] FND-3 blocked") == "FND-3"
    # single-letter prefix is the DEFAULT (mission_key_prefix="M") — must match, or email approvals break
    assert extract_mission_key("Re: [Shipwright] Approval needed — M-151: ship") == "M-151"
    assert extract_mission_key("no key here") is None


def test_email_auth_ok():
    assert email_auth_ok("spf=pass; dkim=pass; dmarc=pass") is True
    assert email_auth_ok("spf=pass dkim=pass") is True                 # aligned SPF+DKIM, no DMARC line
    assert email_auth_ok("spf=fail; dkim=fail; dmarc=fail") is False   # spoof that reached the inbox
    assert email_auth_ok("spf=pass") is False                          # SPF alone is not enough
    assert email_auth_ok("") is False                                  # no auth stamp → not trusted


def test_first_command_line_skips_quotes_and_scaffolding():
    body = "approve\n\nOn Sat, Sept 6, Shipwright wrote:\n> Approval needed — SW-142\n> please review"
    assert first_command_line(body) == "approve"
    # a reply whose first lines are the quoted original still finds the real reply after it
    assert first_command_line("> quoted\n>\nreject") == "reject"
    assert first_command_line("") == ""


def test_sender_allowed_and_parse():
    allowed = parse_allowed("Zain <you@x.com>, mate@y.com; boss@z.com")
    assert allowed == ["you@x.com", "mate@y.com", "boss@z.com"]
    assert sender_allowed("Zain Munir <you@x.com>", allowed) is True
    assert sender_allowed("YOU@X.COM", allowed) is True           # case-insensitive
    assert sender_allowed("stranger@evil.com", allowed) is False  # not on the list
    assert sender_allowed("you@x.com", []) is False               # empty list → deny all


def test_synthesize_command():
    assert synthesize_command("Re: [Shipwright] SW-142", "approve") == "approve SW-142"
    assert synthesize_command("Re: [Shipwright] SW-9", "yes please") == "approve SW-9"
    assert synthesize_command("Re: [Shipwright] SW-9", "no") == "reject SW-9"
    assert synthesize_command("Re: anything", "status") == "status"
    assert synthesize_command("Re: [Shipwright] SW-9", "approve") == "approve SW-9"
    assert synthesize_command("Re: no key", "approve") is None       # approve without a key → nothing
    assert synthesize_command("Re: [Shipwright] SW-9", "thanks!") is None  # not a command


async def test_handle_email_rejects_unknown_sender():
    store = InMemoryStore()
    # even a perfectly-formed, DMARC-passing approve is ignored when the sender isn't allow-listed
    reply = await handle_email("Re: [Shipwright] FND-142", "approve", "evil@x.com",
                               ["owner@x.com"], store, engine=None, auth_results=_AUTH_PASS)
    assert reply is None


async def test_handle_email_rejects_spoofed_from_failing_dmarc():
    store = InMemoryStore()
    # allow-listed From but the auth results show a DMARC failure → spoof, must be rejected
    reply = await handle_email("Re: [Shipwright] FND-142", "approve", "owner@x.com",
                               ["owner@x.com"], store, engine=None,
                               auth_results="spf=fail; dkim=fail; dmarc=fail")
    assert reply is None


async def test_handle_email_approves_from_allowed_sender():
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

    reply = await handle_email("Re: [Shipwright] Approval needed — FND-142: ship",
                               "approve\n\n> original alert text",
                               "Owner <owner@x.com>", ["owner@x.com"], store, _Engine(),
                               auth_results=_AUTH_PASS)
    assert "Approved" in reply
    assert _Engine.seen == (blocker.id, "email")


async def test_handle_email_status_query():
    store = InMemoryStore()
    reply = await handle_email("Re: [Shipwright] anything", "status", "owner@x.com",
                               ["owner@x.com"], store, engine=None, auth_results=_AUTH_PASS)
    assert "Shipwright status" in reply


async def test_handle_email_skips_auth_when_disabled():
    # trusted internal mail with no auth headers still works when require_auth is off
    store = InMemoryStore()
    reply = await handle_email("Re: [Shipwright] anything", "status", "owner@x.com",
                               ["owner@x.com"], store, engine=None, require_auth=False)
    assert "Shipwright status" in reply
