"""WhatsApp inbound endpoints — status queries + approve/reject-by-reply, for both providers.

EVERY request is signature-verified against DB-stored credentials before we act (Twilio's
HMAC-SHA1 over the URL+params, Meta's HMAC-SHA256 over the raw body). Approvals are by mission key
(``approve M-142`` / ``reject M-142``); everything else is a read-only status query.

Point your provider's inbound webhook at:
  * Twilio (Messaging → "A message comes in")  →  POST /api/v1/integrations/whatsapp/twilio
  * Meta   (WhatsApp → Configuration webhook)  →  GET+POST /api/v1/integrations/whatsapp/meta

Both need a public URL (a tunnel in local dev). See docs/notifications.md.
"""
from __future__ import annotations

from urllib.parse import parse_qs
from xml.sax.saxutils import escape

import httpx
from fastapi import APIRouter, Request, Response, status

from ...state import get_engine, get_store
from ...whatsapp_integration import handle_message, verify_meta, verify_twilio

router = APIRouter()

_TIMEOUT = 10.0


async def _config() -> dict:
    """The workspace's ``notify_config`` (DB-stored provider credentials)."""
    prefs = await get_store().get_settings()
    return dict(getattr(prefs, "notify_config", None) or {})


def _twiml(text: str) -> Response:
    """Twilio replies inline via TwiML — no outbound API call needed."""
    body = f"<?xml version='1.0' encoding='UTF-8'?><Response><Message>{escape(text)}</Message></Response>"
    return Response(content=body, media_type="application/xml")


@router.post("/integrations/whatsapp/twilio")
async def whatsapp_twilio(request: Request):
    raw = await request.body()
    form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
    cfg = await _config()
    sig = request.headers.get("X-Twilio-Signature", "")
    if not verify_twilio(str(cfg.get("twilioAuthToken") or ""), str(request.url), form, sig):
        return Response(status_code=status.HTTP_401_UNAUTHORIZED, content="invalid signature")
    reply = await handle_message(form.get("Body", ""), get_store(), get_engine())
    return _twiml(reply)


@router.get("/integrations/whatsapp/meta")
async def whatsapp_meta_verify(request: Request):
    """Meta's one-time webhook verification handshake (echo hub.challenge if the token matches)."""
    params = request.query_params
    cfg = await _config()
    token = str(cfg.get("whatsappVerifyToken") or "")
    if params.get("hub.mode") == "subscribe" and token and params.get("hub.verify_token") == token:
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    return Response(status_code=status.HTTP_403_FORBIDDEN, content="verification failed")


@router.post("/integrations/whatsapp/meta")
async def whatsapp_meta(request: Request):
    raw = await request.body()
    cfg = await _config()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not verify_meta(str(cfg.get("whatsappAppSecret") or ""), raw, sig):
        return Response(status_code=status.HTTP_401_UNAUTHORIZED, content="invalid signature")
    text, sender = _parse_meta(raw)
    if text and sender:
        reply = await handle_message(text, get_store(), get_engine())
        await _send_meta_reply(cfg, sender, reply)
    return {"ok": True}  # Meta requires a fast 200; the reply goes out via the send API


def _parse_meta(raw: bytes) -> tuple[str, str]:
    """Pull the first inbound message's text + sender from a Meta webhook body. ('', '') if none."""
    import json
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
        msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
        return str(msg.get("text", {}).get("body", "")), str(msg.get("from", ""))
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return "", ""  # status callbacks and other events carry no message — ignore them


async def _send_meta_reply(cfg: dict, to: str, text: str) -> None:
    """Reply to the inbound sender via the Meta Cloud API (best-effort — a failed reply never 500s)."""
    token = str(cfg.get("whatsappToken") or "")
    phone_id = str(cfg.get("whatsappPhoneId") or "")
    if not (token and phone_id and to):
        return
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            await client.post(
                f"https://graph.facebook.com/v20.0/{phone_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"messaging_product": "whatsapp", "to": to, "type": "text",
                      "text": {"body": text}},
            )
    except Exception:  # noqa: BLE001 — the inbound webhook must still return 200 to Meta
        pass
