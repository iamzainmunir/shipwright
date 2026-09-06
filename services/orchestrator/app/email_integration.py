"""Email two-way — turn a reply to a Shipwright alert into a command.

Unlike Slack/WhatsApp, email has no cryptographic signature, so the trust boundary is a **sender
allow-list** (default: the workspace's notify recipient): a reply is only acted on when it comes
from an address we already send to. The mission key is read from the (``Re:``) subject line, the
command word from the first non-quoted body line — so replying "approve" to an "Approval needed —
SW-142" alert resolves SW-142's gate. Delivery is by IMAP polling (see :mod:`app.email_poller`),
so it needs no public URL.
"""
from __future__ import annotations

import re
from email.utils import getaddresses, parseaddr

from .inbound import _APPROVE_WORDS, _REJECT_WORDS, resolve_text

# Mission keys look like M-151 / SW-142 / FND-3 — a 1-10 char upper-case prefix, a dash, digits.
# The prefix is configurable and defaults to a single letter ("M"), so it must allow length 1.
_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{0,9}-\d+)\b")
_STATUS_WORDS = frozenset({"status", "missions", "mission", "tickets", "models", "help"})


def extract_mission_key(text: str) -> str | None:
    """First mission key found in ``text`` (subject or body), or None."""
    m = _KEY_RE.search(text or "")
    return m.group(1) if m else None


def first_command_line(body: str) -> str:
    """The first meaningful line of an email body — skips blanks, quoted (``>``) lines, and the
    ``On <date>, X wrote:`` / signature scaffolding a reply tacks on."""
    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line or line.startswith((">", "|")):
            continue
        low = line.lower()
        if low.startswith(("on ", "from:", "sent:", "to:", "subject:", "-----", "____", "--")):
            continue
        return line
    return ""


def sender_allowed(from_addr: str, allowed: list[str]) -> bool:
    """True iff ``from_addr`` is one of the allow-listed addresses (case-insensitive)."""
    addr = parseaddr(from_addr or "")[1].lower()
    if not addr:
        return False
    return any(addr == parseaddr(a)[1].lower() for a in allowed if a)


def parse_allowed(raw: str) -> list[str]:
    """Parse the comma/semicolon-separated ``emailAllowedSenders`` setting into addresses."""
    return [addr for _, addr in getaddresses([(raw or "").replace(";", ",")]) if addr]


def email_auth_ok(auth_results: str) -> bool:
    """True iff the receiving mail server's ``Authentication-Results`` show a passing **DMARC** (or
    aligned SPF+DKIM). This is the defense against a spoofed ``From`` that slipped into the mailbox:
    the allow-list says *who* may act, this says the ``From`` is *genuinely* them. Empty (no auth
    stamp) or a failing result → not ok. Callers may disable via ``require_auth`` for trusted
    internal mail that carries no auth headers."""
    text = (auth_results or "").lower()
    if not text:
        return False
    if "dmarc=pass" in text:
        return True
    return "spf=pass" in text and "dkim=pass" in text


def synthesize_command(subject: str, body: str) -> str | None:
    """Turn a reply's subject+body into a canonical command string, or None if it isn't one."""
    line = first_command_line(body)
    word = (line.split()[0].lower() if line else "")
    if word in _APPROVE_WORDS or word in _REJECT_WORDS:
        key = extract_mission_key(subject) or extract_mission_key(line)
        verb = "approve" if word in _APPROVE_WORDS else "reject"
        return f"{verb} {key}" if key else None
    if word in _STATUS_WORDS:
        return line  # pass through — e.g. "status", "mission SW-142"
    return None


async def handle_email(subject: str, body: str, from_addr: str, allowed: list[str],
                       store, engine, *, auth_results: str = "", require_auth: bool = True) -> str | None:
    """Resolve one inbound email reply → the reply text to send back, or None to ignore it.

    Two gates before any action (both must pass): the sender is on the **allow-list**, and — unless
    ``require_auth`` is disabled — the ``From`` passed **DMARC/SPF+DKIM** per ``auth_results`` (so a
    spoofed From that reached the inbox is rejected). Unknown sender / failed auth / non-command all
    return None silently (no reply → no mail loop)."""
    if not sender_allowed(from_addr, allowed):
        return None
    if require_auth and not email_auth_ok(auth_results):
        return None
    command = synthesize_command(subject, body)
    if command is None:
        return None
    return await resolve_text(command, store, engine, actor="email")
