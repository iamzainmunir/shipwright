"""Outbound notifications on mission events — email, WhatsApp (Twilio / Meta Cloud API), Slack.

Design mirrors the rest of the platform: **offline by default, real backend is a drop-in**, and
**failure-isolated** (the Observer rule — a notification never fails a run). Which channels and
events fire, and the recipient email / WhatsApp number, are per-workspace preferences set in
Settings; the *provider credentials* come from the environment so secrets never touch the DB.
Any channel whose credentials or recipient are missing is silently skipped.

Environment (all optional; a channel with no creds is a no-op):

  Email (SMTP):   SHIPWRIGHT_SMTP_HOST, _SMTP_PORT (default 587), _SMTP_USER, _SMTP_PASSWORD,
                  _SMTP_FROM (default _SMTP_USER), _SMTP_TLS ("1" default; "0" for plain/local)
  WhatsApp Twilio: SHIPWRIGHT_TWILIO_ACCOUNT_SID, _TWILIO_AUTH_TOKEN,
                   _TWILIO_WHATSAPP_FROM (e.g. "whatsapp:+14155238886")
  WhatsApp Meta:   SHIPWRIGHT_WHATSAPP_TOKEN, _WHATSAPP_PHONE_ID
  Slack/webhook:   SHIPWRIGHT_SLACK_WEBHOOK (an incoming-webhook URL)

Legacy FOUNDRY_* names are still honoured for every variable above.
"""
from __future__ import annotations

import asyncio
import os
import smtplib
import ssl
from email.message import EmailMessage

import httpx
import structlog
from foundry_core.enums import BlockerKind
from foundry_core.models import Blocker, Mission

log = structlog.get_logger(__name__)

# Event keys — must match the keys the Settings UI writes into notify_events.
EV_BLOCKER = "blocker"
EV_COMPLETED = "completed"
EV_FAILED = "failed"
EV_APPROVAL = "approval"

# Channel keys — must match the keys the Settings UI writes into notify_channels.
CH_EMAIL = "email"
CH_TWILIO = "whatsapp_twilio"
CH_META = "whatsapp_meta"
CH_SLACK = "slack"

_TIMEOUT = 10.0


def _env(name: str, default: str = "") -> str:
    """Read ``SHIPWRIGHT_<name>``, falling back to the legacy ``FOUNDRY_<name>``, then default."""
    return os.getenv(f"SHIPWRIGHT_{name}") or os.getenv(f"FOUNDRY_{name}") or default


class Notifier:
    """Dispatches mission-event notifications through the workspace's enabled channels.

    Instantiated once by the engine and called at the semantic points (blocker raised, mission
    completed, mission failed). Every public method is safe to call and never raises.
    """

    def __init__(self, store) -> None:
        self._store = store

    # ---- engine entry points ----------------------------------------------------
    async def on_blocker(self, blocker: Blocker, mission: Mission) -> None:
        is_approval = blocker.kind == BlockerKind.APPROVAL
        event = EV_APPROVAL if is_approval else EV_BLOCKER
        head = "Approval needed" if is_approval else "Mission blocked"
        await self._dispatch(
            mission, event,
            subject=f"[Shipwright] {head} — {mission.key}: {mission.title}",
            body=f"{head} on {mission.key} ({mission.title}).\n\n{self._blocker_detail(blocker)}",
        )

    async def on_completed(self, mission: Mission) -> None:
        await self._dispatch(
            mission, EV_COMPLETED,
            subject=f"[Shipwright] Shipped — {mission.key}: {mission.title}",
            body=f"{mission.key} ({mission.title}) completed and shipped successfully. 🎉",
        )

    async def on_failed(self, mission: Mission, message: str = "") -> None:
        await self._dispatch(
            mission, EV_FAILED,
            subject=f"[Shipwright] Halted — {mission.key}: {mission.title}",
            body=f"{mission.key} ({mission.title}) halted and needs your attention.\n\n{message}".strip(),
        )

    # ---- dispatch ---------------------------------------------------------------
    async def _dispatch(self, mission: Mission, event: str, *, subject: str, body: str) -> None:
        try:
            prefs = await self._store.get_settings(mission.workspace_id)
        except Exception as exc:  # pragma: no cover - store hiccup must not fail a run
            log.warning("notify.prefs_failed", error=str(exc))
            return
        if prefs is None or not getattr(prefs, "notify_enabled", False):
            return
        events = getattr(prefs, "notify_events", None) or {}
        if not events.get(event, False):
            return
        channels = getattr(prefs, "notify_channels", None) or {}
        senders = {
            CH_EMAIL: lambda: self._send_email(prefs, subject, body),
            CH_TWILIO: lambda: self._send_twilio(prefs, subject, body),
            CH_META: lambda: self._send_meta(prefs, subject, body),
            CH_SLACK: lambda: self._send_slack(subject, body),
        }
        for key, make in senders.items():
            if not channels.get(key, False):
                continue
            try:
                await make()
            except Exception as exc:  # one channel failing never blocks the others / the run
                log.warning("notify.channel_failed", channel=key, mission=mission.key, error=str(exc))

    @staticmethod
    def _blocker_detail(blocker: Blocker) -> str:
        detail = (blocker.detail or "").strip()
        if detail.startswith("{"):  # question blockers stash JSON — keep the message readable
            import json
            try:
                data = json.loads(detail)
                qs = data.get("questions")
                if isinstance(qs, list) and qs:
                    return "Questions:\n" + "\n".join(f"  • {q}" for q in qs)
            except Exception:  # pragma: no cover - best effort
                pass
        return detail or f"Kind: {blocker.kind}"

    # ---- channels (each skips silently when unconfigured) -----------------------
    async def _send_email(self, prefs, subject: str, body: str) -> None:
        host = _env("SMTP_HOST").strip()
        to_addr = (getattr(prefs, "notify_email", "") or "").strip()
        if not host or not to_addr:
            return
        user = _env("SMTP_USER").strip()
        from_addr = _env("SMTP_FROM").strip() or user or "shipwright@localhost"
        port = int(_env("SMTP_PORT", "587") or "587")
        password = _env("SMTP_PASSWORD")
        use_tls = _env("SMTP_TLS", "1").strip().lower() not in {"0", "false", "no", "off"}

        def _send() -> None:
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = subject, from_addr, to_addr
            msg.set_content(body)
            with smtplib.SMTP(host, port, timeout=_TIMEOUT) as smtp:
                if use_tls:
                    smtp.starttls(context=ssl.create_default_context())
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)

        await asyncio.to_thread(_send)  # smtplib is blocking

    async def _send_twilio(self, prefs, subject: str, body: str) -> None:
        sid = _env("TWILIO_ACCOUNT_SID").strip()
        token = _env("TWILIO_AUTH_TOKEN").strip()
        from_wa = _env("TWILIO_WHATSAPP_FROM").strip()
        to = (getattr(prefs, "notify_whatsapp", "") or "").strip()
        if not (sid and token and from_wa and to):
            return
        to_wa = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                auth=(sid, token),
                data={"From": from_wa, "To": to_wa, "Body": f"{subject}\n\n{body}"},
            )
            resp.raise_for_status()

    async def _send_meta(self, prefs, subject: str, body: str) -> None:
        token = _env("WHATSAPP_TOKEN").strip()
        phone_id = _env("WHATSAPP_PHONE_ID").strip()
        to = (getattr(prefs, "notify_whatsapp", "") or "").strip().removeprefix("whatsapp:").lstrip("+")
        if not (token and phone_id and to):
            return
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"https://graph.facebook.com/v20.0/{phone_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "text",
                    "text": {"body": f"{subject}\n\n{body}"},
                },
            )
            resp.raise_for_status()

    async def _send_slack(self, subject: str, body: str) -> None:
        webhook = _env("SLACK_WEBHOOK").strip()
        if not webhook:
            return
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(webhook, json={"text": f"*{subject}*\n{body}"})
            resp.raise_for_status()
