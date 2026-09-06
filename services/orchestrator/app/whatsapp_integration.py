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

from foundry_core.enums import ApprovalDecision, BlockerKind

from .slack_integration import resolve_command


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


async def handle_message(text: str, store, engine) -> str:
    """Parse an inbound WhatsApp message → a reply string."""
    parts = (text or "").strip().split()
    if not parts:
        return "Send `status`, `missions`, `mission <KEY>`, or `approve/reject <KEY>`."
    cmd = parts[0].lower()

    if cmd in ("approve", "reject") and len(parts) > 1:
        key = parts[1]
        mission = await store.get_mission(key)
        if mission is None:
            return f"Mission {key} not found."
        pending = [
            b for b in await store.list_blockers()
            if b.mission_id == mission.id and b.kind in (BlockerKind.APPROVAL, "approval")
        ]
        if not pending:
            return f"No pending approval on {key}."
        decision = ApprovalDecision.APPROVE if cmd == "approve" else ApprovalDecision.REJECT
        try:
            await engine.resolve_blocker(pending[0].id, decision, actor="whatsapp", note="via WhatsApp")
        except Exception as exc:  # noqa: BLE001 — reply with the error, never 500 the webhook
            return f"Couldn't resolve {key}: {exc}"
        return f"{'✅ Approved' if cmd == 'approve' else '🛑 Rejected'} {key}."

    # status/missions/mission/tickets/models/help — reuse the shared resolver's text
    result = await resolve_command(text, store)
    return result.get("text", "OK")
