"""WhatsApp two-way integration (Twilio + Meta Cloud API) — inbound status queries and
approve/reject-by-reply.

**Security-critical:** every inbound request is signature-verified per provider before we act —
Twilio's HMAC-SHA1 over the URL+params, Meta's HMAC-SHA256 over the raw body. Credentials live in
the DB (``AutonomyPolicy.notify_config``), configured in the UI — never ``.env``.

Unlike a Slack button, a WhatsApp reply is free text, so approvals are by mission key:
``approve M-142`` / ``reject M-142`` resolves that mission's open approval gate. Everything else
(``status``, ``missions``, ``mission <KEY>``, …) reuses the shared status resolver.
"""
from __future__ import annotations

import base64
import hashlib
import hmac

from .inbound import resolve_text


def verify_twilio(auth_token: str, url: str, params: dict[str, str], signature: str) -> bool:
    """Twilio signs ``url + concat(k+v for k in sorted(params))`` with HMAC-SHA1 → base64."""
    if not (auth_token and signature):
        return False
    base = url + "".join(k + params[k] for k in sorted(params))
    digest = hmac.new(auth_token.encode(), base.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


def verify_meta(app_secret: str, raw_body: bytes, signature: str) -> bool:
    """Meta signs the raw body with HMAC-SHA256 → ``sha256=<hex>`` (the X-Hub-Signature-256 header)."""
    if not (app_secret and signature):
        return False
    expected = "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_numbers(raw: str) -> list[str]:
    """Parse a comma/semicolon-separated list of phone numbers."""
    return [p.strip() for p in (raw or "").replace(";", ",").split(",") if p.strip()]


def _digits(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def number_allowed(from_raw: str, allowed: list[str]) -> bool:
    """True iff the inbound WhatsApp sender is on the allow-list (compared by digits only, so
    ``whatsapp:+1 415…`` and ``1415…`` match). A signed webhook only proves the message came via
    *your* provider account — this proves *who* sent it, the way email gates on the sender."""
    sender = _digits(from_raw)
    if not sender or not allowed:
        return False
    return any(_digits(a) == sender for a in allowed if _digits(a))


async def handle_message(text: str, store, engine) -> str:
    """Parse an inbound WhatsApp message → a reply string (shared brain, attributed to WhatsApp)."""
    return await resolve_text(text, store, engine, actor="whatsapp")
