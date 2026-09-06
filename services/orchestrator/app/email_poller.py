"""Two-way Email over **IMAP polling** — reply to a Shipwright alert with ``approve SW-142`` or
``status`` and it acts. Needs **no public URL** (we reach out to the mailbox), so it works with any
plain SMTP+IMAP account (a Gmail App Password, etc.).

Offline-first (Rule 0): inert until the email channel + ``imapHost`` are configured. Failure-isolated:
a bad poll logs and retries, never crashing the app. The trust boundary is the sender allow-list
(see :mod:`app.email_integration`) — replies from unknown senders are never acted on and never
touched (left unread in the mailbox). Blocking IMAP I/O runs in a worker thread; the SMTP reply and
the command resolution run on the event loop.
"""
from __future__ import annotations

import asyncio
import contextlib
import email
import imaplib
from email.header import decode_header, make_header
from email.utils import parseaddr

import structlog

from .email_integration import handle_email, parse_allowed, sender_allowed
from .notifier import CH_EMAIL, clean_header, clean_secret, send_email

log = structlog.get_logger(__name__)

_INTERVAL = 30.0  # seconds between mailbox polls
_TIMEOUT = 30.0   # IMAP socket timeout — a black-holed server must not park the loop forever


async def run_forever(store, engine, *, interval: float = _INTERVAL) -> None:
    with contextlib.suppress(asyncio.CancelledError):
        while True:
            try:
                await _poll_once(store, engine)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a bad poll must never crash the app
                log.warning("email.poll_error", error=str(exc))
            await asyncio.sleep(interval)


async def _poll_once(store, engine) -> None:
    prefs = await store.get_settings()
    if not getattr(prefs, "notify_enabled", False):
        return
    if not (getattr(prefs, "notify_channels", None) or {}).get(CH_EMAIL, False):
        return
    cfg = getattr(prefs, "notify_config", None) or {}
    if not str(cfg.get("imapHost") or "").strip():
        return  # two-way email not configured — outbound alerts still work without IMAP

    allowed = parse_allowed(cfg.get("emailAllowedSenders") or getattr(prefs, "notify_email", "") or "")
    if not allowed:
        return  # no allow-list → nothing may be acted on (fail safe)
    require_auth = str(cfg.get("emailVerifyAuth", "1")).lower() not in {"0", "false", "no", "off"}

    messages = await asyncio.to_thread(_fetch_unseen, cfg)
    done: list[bytes] = []  # UIDs to mark \Seen — only messages we finished handling
    for msg in messages:
        if not sender_allowed(msg["from"], allowed):
            continue  # stranger's mail — leave it unread, never act
        try:
            reply = await handle_email(msg["subject"], msg["body"], msg["from"], allowed,
                                       store, engine, auth_results=msg["auth"], require_auth=require_auth)
        except Exception as exc:  # noqa: BLE001 — isolate per message; a failure retries next poll
            log.warning("email.handle_error", error=str(exc))
            continue  # NOT added to `done` → not marked seen → retried, not lost
        done.append(msg["uid"])  # handled (gate resolved or intentionally ignored) — don't reprocess
        if reply:
            to_addr = parseaddr(msg["from"])[1]
            subject = msg["subject"] if msg["subject"].lower().startswith("re:") else f"Re: {msg['subject']}"
            with contextlib.suppress(Exception):
                await send_email(prefs, to_addr, subject, reply)
    if done:
        await asyncio.to_thread(_mark_seen, cfg, done)


# ---- blocking IMAP helpers (run via asyncio.to_thread) --------------------------
def _connect(cfg: dict) -> imaplib.IMAP4:
    host = clean_header(str(cfg["imapHost"]))
    port = int(str(cfg.get("imapPort") or "993") or "993")
    user = clean_header(str(cfg.get("imapUser") or cfg.get("smtpUser") or ""))
    password = clean_secret(str(cfg.get("imapPassword") or cfg.get("smtpPassword") or ""))
    ssl_on = str(cfg.get("imapSsl", "1")).lower() not in {"0", "false", "no", "off"}
    conn: imaplib.IMAP4 = (
        imaplib.IMAP4_SSL(host, port, timeout=_TIMEOUT) if ssl_on
        else imaplib.IMAP4(host, port, timeout=_TIMEOUT)
    )
    conn.login(user, password)
    return conn


def _fetch_unseen(cfg: dict) -> list[dict]:
    """Unseen replies to our alerts (subject contains 'Shipwright'), fetched WITHOUT marking read.

    Uses UID commands throughout — UIDs are stable across sessions, so the fetch here and the
    :func:`_mark_seen` on a later connection always target the same messages even if the mailbox is
    expunged in between (sequence numbers would silently shift)."""
    conn = _connect(cfg)
    try:
        conn.select("INBOX")
        typ, data = conn.uid("SEARCH", None, '(UNSEEN SUBJECT "Shipwright")')
        if typ != "OK" or not data or not data[0]:
            return []
        out: list[dict] = []
        for uid in data[0].split():
            typ, payload = conn.uid("FETCH", uid, "(BODY.PEEK[])")  # PEEK: don't set \Seen
            if typ != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            msg = email.message_from_bytes(payload[0][1])
            out.append({
                "uid": uid,
                "subject": _header(msg.get("Subject")),
                "from": _header(msg.get("From")),
                "body": _plain_text(msg),
                "auth": " ".join(msg.get_all("Authentication-Results") or []),
            })
        return out
    finally:
        with contextlib.suppress(Exception):
            conn.logout()


def _mark_seen(cfg: dict, uids: list[bytes]) -> None:
    conn = _connect(cfg)
    try:
        conn.select("INBOX")
        for uid in uids:
            with contextlib.suppress(Exception):
                conn.uid("STORE", uid, "+FLAGS", "\\Seen")
    finally:
        with contextlib.suppress(Exception):
            conn.logout()


def _header(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:  # noqa: BLE001 - malformed header → best effort
        return raw


def _plain_text(msg: email.message.Message) -> str:
    """The message's text/plain part (first one), decoded; falls back to the whole payload."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(
                part.get("Content-Disposition", "")
            ):
                return _decode(part)
        return ""
    return _decode(msg)


def _decode(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return str(part.get_payload() or "")
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, "replace")
    except (LookupError, ValueError):
        return payload.decode("utf-8", "replace")
