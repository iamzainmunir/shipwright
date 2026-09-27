"""Outbound notifications on mission events — email, WhatsApp (Twilio / Meta Cloud API), Slack.

Design mirrors the rest of the platform: **offline by default, real backend is a drop-in**, and
**failure-isolated** (the Observer rule — a notification never fails a run). Which channels and
events fire, the recipient email / WhatsApp number, AND the provider credentials are per-workspace
settings **stored in the DB and configured in the UI** (``AutonomyPolicy.notify_config``); secrets
are redacted on read + merged on write by the API. Any channel whose credentials or recipient are
missing is silently skipped.

``notify_config`` keys (camelCase): ``smtpHost/smtpPort/smtpUser/smtpPassword/smtpFrom/smtpTls``,
``twilioAccountSid/twilioAuthToken/twilioWhatsappFrom``, ``whatsappToken/whatsappPhoneId``,
``slackWebhook``. Each falls back to the matching ``SHIPWRIGHT_/FOUNDRY_*`` env var (for
ops/deploy) when the DB value is absent — see :func:`_cfg`.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import os
import smtplib
import ssl
from email.message import EmailMessage

import httpx
import structlog
from foundry_core.enums import BlockerKind
from foundry_core.models import Blocker, Mission

from .slack_integration import approval_blocks

log = structlog.get_logger(__name__)

# Event keys — must match the keys the Settings UI writes into notify_events.
EV_BLOCKER = "blocker"
EV_COMPLETED = "completed"
EV_FAILED = "failed"
EV_APPROVAL = "approval"
EV_QUESTION = "question"   # the AI asked a clarifying question — answerable from chat (P4)

# Channel keys — must match the keys the Settings UI writes into notify_channels.
CH_EMAIL = "email"
CH_TWILIO = "whatsapp_twilio"
CH_META = "whatsapp_meta"
CH_SLACK = "slack"

_TIMEOUT = 10.0


def _env(name: str, default: str = "") -> str:
    """Read ``SHIPWRIGHT_<name>``, falling back to the legacy ``FOUNDRY_<name>``, then default."""
    return os.getenv(f"SHIPWRIGHT_{name}") or os.getenv(f"FOUNDRY_{name}") or default


def _friendly_error(raw: str) -> str:
    """Translate a raw provider/stdlib error into a short, actionable hint for the Settings UI."""
    low = raw.lower()
    if "protocol" in low and "http" in low:
        return "Webhook URL must start with https:// — paste the full Incoming Webhook URL."
    if "ascii" in low and "encode" in low:
        return "A field contains an invalid character (e.g. a pasted non-breaking space) — retype it."
    if "authentication" in low or "username and password" in low or "5.7.8" in low or "invalid_auth" in low:
        return "Login failed — check the username/token (Gmail needs an App Password, not your login)."
    if "getaddrinfo" in low or "name or service" in low or "nodename" in low:
        return "Host not found — check the server address."
    if "timed out" in low or "timeout" in low:
        return "Connection timed out — check the host/port and your network."
    return raw[:140]


def _cfg(prefs, key: str, env_name: str, default: str = "") -> str:
    """A notification setting: prefer the DB-stored ``notify_config[key]`` (configured in the UI),
    fall back to the legacy ``SHIPWRIGHT_/FOUNDRY_<env_name>`` env var, then the default."""
    value = str((getattr(prefs, "notify_config", None) or {}).get(key) or "").strip()
    return value or _env(env_name, default)


def clean_header(value: str) -> str:
    """Strip characters that crash smtplib's (ASCII) envelope commands from a header/address —
    notably the non-breaking space (``\\xa0``) and zero-width chars that sneak in via copy-paste."""
    return (value or "").replace("\xa0", " ").replace("​", "").replace("﻿", "").strip()


def clean_secret(value: str) -> str:
    """Normalize a credential (SMTP/IMAP password, token). Gmail App Passwords are shown as four
    space-separated groups (often with non-breaking spaces) but must be sent with NO whitespace —
    and IMAP/SMTP encode commands as ASCII, so a stray ``\\xa0`` crashes login. Strip all whitespace."""
    return "".join((value or "").split())


def normalize_url(url: str) -> str:
    """Make a pasted webhook URL forgiving: strip stray whitespace and prepend ``https://`` when the
    scheme was left off (a common paste mistake), so ``hooks.slack.com/…`` still works."""
    url = clean_header(url)
    if url and not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url


async def send_email(prefs, to_addr: str, subject: str, body: str) -> None:
    """Send one email via the workspace's SMTP settings to an arbitrary recipient. Shared by the
    outbound alerts (recipient = ``notify_email``) and the two-way IMAP poller (recipient = the
    person who replied). No-op when SMTP or the recipient is unconfigured."""
    host = clean_header(_cfg(prefs, "smtpHost", "SMTP_HOST"))
    to_addr = clean_header(to_addr)
    if not (host and to_addr):
        return
    subject = clean_header(subject)
    user = clean_header(_cfg(prefs, "smtpUser", "SMTP_USER"))
    from_addr = clean_header(_cfg(prefs, "smtpFrom", "SMTP_FROM") or user or "shipwright@localhost")
    port = int(_cfg(prefs, "smtpPort", "SMTP_PORT", "587") or "587")
    password = clean_secret(_cfg(prefs, "smtpPassword", "SMTP_PASSWORD"))
    use_tls = _cfg(prefs, "smtpTls", "SMTP_TLS", "1").lower() not in {"0", "false", "no", "off"}

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


class Notifier:
    """Dispatches mission-event notifications through the workspace's enabled channels.

    Instantiated once by the engine and called at the semantic points (blocker raised, mission
    completed, mission failed). Every public method is safe to call and never raises.
    """

    def __init__(self, store) -> None:
        self._store = store

    # ---- engine entry points ----------------------------------------------------
    async def on_blocker(self, blocker: Blocker, mission: Mission) -> None:
        # P4: an AI clarifying question is pushable to chat with a reply protocol, and it ARMS the
        # WhatsApp session so the user's next free-text reply routes back to submit_clarification.
        if blocker.kind == BlockerKind.QUESTION or blocker.kind == "question":
            detail = self._blocker_detail(blocker)
            subject = f"[Shipwright] Question — {mission.key}: {mission.title}"
            body = (f"I need your input on {mission.key} ({mission.title}).\n\n{detail}\n\n"
                    "Reply here with your answer.")
            await self._arm_wa_question(mission, blocker)
            await self._dispatch(mission, EV_QUESTION, subject=subject, body=body)
            return
        is_approval = blocker.kind == BlockerKind.APPROVAL
        event = EV_APPROVAL if is_approval else EV_BLOCKER
        head = "Approval needed" if is_approval else "Mission blocked"
        subject = f"[Shipwright] {head} — {mission.key}: {mission.title}"
        body = f"{head} on {mission.key} ({mission.title}).\n\n{self._blocker_detail(blocker)}"
        # An approval gate gets interactive Approve/Reject buttons on Slack (resolved via the signed
        # /integrations/slack/interactivity endpoint).
        slack_blocks = approval_blocks(subject, body, blocker.id) if is_approval else None
        await self._dispatch(mission, event, subject=subject, body=body, slack_blocks=slack_blocks)

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

    async def send_test(self, workspace_id: str) -> dict:
        """Send a test message to every *enabled* channel (ignores the event filter) — powers the
        Settings "Send test" button. Returns a per-channel breakdown so the UI can tell the user
        exactly what happened: ``{"sent": [...], "skipped": [...], "failed": [{channel, error}]}``.
        A channel missing credentials is *skipped* (not failed); a channel that errored reports why."""
        result: dict = {"sent": [], "skipped": [], "failed": []}
        try:
            prefs = await self._store.get_settings(workspace_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("notify.test_prefs_failed", error=str(exc))
            return result
        channels = getattr(prefs, "notify_channels", None) or {}
        subject = "[Shipwright] Test notification"
        body = "Your Shipwright notifications are working — this is a test. ✅"
        senders = {
            CH_EMAIL: self._send_email, CH_TWILIO: self._send_twilio,
            CH_META: self._send_meta, CH_SLACK: self._send_slack,
        }
        for key, fn in senders.items():
            if not channels.get(key, False):
                continue
            if not self._channel_ready(prefs, key):
                result["skipped"].append(key)
                continue
            try:
                await fn(prefs, subject, body)
                result["sent"].append(key)
            except Exception as exc:  # noqa: BLE001 — report per-channel, never raise
                log.warning("notify.test_failed", channel=key, error=str(exc))
                result["failed"].append({"channel": key, "error": _friendly_error(str(exc))})
        return result

    @staticmethod
    def _channel_ready(prefs, key: str) -> bool:
        """Whether a channel has the minimum credentials to even attempt a send."""
        if key == CH_EMAIL:
            return bool(_cfg(prefs, "smtpHost", "SMTP_HOST") and (getattr(prefs, "notify_email", "") or "").strip())
        if key == CH_TWILIO:
            return bool(_cfg(prefs, "twilioAccountSid", "TWILIO_ACCOUNT_SID")
                        and _cfg(prefs, "twilioAuthToken", "TWILIO_AUTH_TOKEN")
                        and _cfg(prefs, "twilioWhatsappFrom", "TWILIO_WHATSAPP_FROM")
                        and (getattr(prefs, "notify_whatsapp", "") or "").strip())
        if key == CH_META:
            return bool(_cfg(prefs, "whatsappToken", "WHATSAPP_TOKEN")
                        and _cfg(prefs, "whatsappPhoneId", "WHATSAPP_PHONE_ID")
                        and (getattr(prefs, "notify_whatsapp", "") or "").strip())
        if key == CH_SLACK:
            return bool(_cfg(prefs, "slackWebhook", "SLACK_WEBHOOK"))
        return False

    # ---- dispatch ---------------------------------------------------------------
    async def _dispatch(self, mission: Mission, event: str, *, subject: str, body: str,
                        slack_blocks: list | None = None) -> None:
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
            CH_SLACK: lambda: self._send_slack(prefs, subject, body, slack_blocks),
        }
        for key, make in senders.items():
            if not channels.get(key, False):
                continue
            try:
                await make()
            except Exception as exc:  # one channel failing never blocks the others / the run
                log.warning("notify.channel_failed", channel=key, mission=mission.key, error=str(exc))

    async def _arm_wa_question(self, mission: Mission, blocker: Blocker) -> None:
        """Put the primary WhatsApp recipient's session into AWAITING_ANSWER for this question blocker,
        so their next plain-text reply is routed to the right mission's clarification (P4). Best-effort:
        skipped when WhatsApp isn't configured; never raises."""
        upsert = getattr(self._store, "upsert_wa_session", None)
        if upsert is None:
            return
        try:
            prefs = await self._store.get_settings(mission.workspace_id)
            channels = getattr(prefs, "notify_channels", None) or {}
            if not (channels.get(CH_TWILIO) or channels.get(CH_META)):
                return  # no WhatsApp channel → nothing to arm
            from .whatsapp_integration import normalize_sender
            recipients = [getattr(prefs, "notify_whatsapp", "") or ""]
            cfg = getattr(prefs, "notify_config", None) or {}
            recipients += (cfg.get("whatsappAllowedSenders") or "").replace(";", ",").split(",")
            questions = []
            detail = (blocker.detail or "").strip()
            if detail.startswith("{"):
                import json
                with contextlib.suppress(Exception):
                    questions = json.loads(detail).get("questions") or []
            from foundry_core.ids import new_ulid
            from foundry_core.models import WaSession
            now = _dt.datetime.now(_dt.UTC)
            for raw in recipients:
                key = normalize_sender(raw)
                if not key:
                    continue
                existing = None
                with contextlib.suppress(Exception):
                    existing = await self._store.get_wa_session(mission.workspace_id, key)
                await upsert(WaSession(
                    id=(existing.id if existing else new_ulid()),
                    workspace_id=mission.workspace_id, sender=key, state="awaiting_answer",
                    context={"mission_key": mission.key, "blocker_id": blocker.id, "questions": questions},
                    updated_at=now, expires_at=now + _dt.timedelta(seconds=1800),
                ))
        except Exception as exc:  # noqa: BLE001 — arming is best-effort, never fail a run
            log.warning("notify.arm_wa_failed", error=str(exc))

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
        await send_email(prefs, getattr(prefs, "notify_email", "") or "", subject, body)

    async def _send_twilio(self, prefs, subject: str, body: str) -> None:
        sid = _cfg(prefs, "twilioAccountSid", "TWILIO_ACCOUNT_SID")
        token = _cfg(prefs, "twilioAuthToken", "TWILIO_AUTH_TOKEN")
        from_wa = _cfg(prefs, "twilioWhatsappFrom", "TWILIO_WHATSAPP_FROM")
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
        token = _cfg(prefs, "whatsappToken", "WHATSAPP_TOKEN")
        phone_id = _cfg(prefs, "whatsappPhoneId", "WHATSAPP_PHONE_ID")
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

    async def _send_slack(self, prefs, subject: str, body: str, blocks: list | None = None) -> None:
        webhook = normalize_url(_cfg(prefs, "slackWebhook", "SLACK_WEBHOOK"))
        if not webhook:
            return
        payload = {"blocks": blocks} if blocks else {"text": f"*{subject}*\n{body}"}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(webhook, json=payload)
            resp.raise_for_status()
