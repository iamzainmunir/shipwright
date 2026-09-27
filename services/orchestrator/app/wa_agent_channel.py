"""WhatsApp Agent Platform channel (P5) — a no-tunnel transport for the P4 control plane.

The user creates a "Shipwright" agent inside WhatsApp (Settings → Agents), pastes its API token into
Settings → Notifications → WhatsApp Agent, and drives their org from a normal WhatsApp chat. The receive
model is a **server-side long-poll** (`GET /agent/v1/updates`) — no Cloud-API app, no webhook, no public
URL. It reuses the exact P4 brain (:func:`app.wa_inbound.handle_stateful`); this module owns only *how
this channel receives, throttles, replies, and tracks its offset cursor*.

Offline-first (Rule 0): inert until ``notify_config.whatsappAgentKey`` is set. Failure-isolated: a bad
poll logs and retries, never crashing the app. Contract per the WhatsApp Agent Platform Developer Manual
v1: Bearer token, `to` is always ``user:<id>``, text ≤4096, updates retained 30 days, offset discipline
(persist ``next_offset``), rate limits (15/min updates, 12/min sends) with 429/503 backoff.

PRIVACY: this channel is NOT end-to-end encrypted — messages carry the minimum (mission key + short ask),
never secrets/diffs/logs. Identifiers are opaque and may change → treat ``from`` as the conversation key,
never render it to a user.
"""
from __future__ import annotations

import asyncio
import contextlib

import httpx
import structlog

from .wa_inbound import handle_stateful

log = structlog.get_logger(__name__)

_BASE_URL = "https://api.whatsapp.com/agent/v1"
_POLL_TIMEOUT_S = 15      # long-poll hold (manual: 0–25)
_POLL_LIMIT = 50          # updates per poll (manual: ≤100)
_READ_TIMEOUT_S = 40.0    # client read timeout — well above the long-poll hold
_MAX_TEXT = 4096          # manual: text body cap
_SEND_MIN_INTERVAL = 5.0  # self-throttle sends (≤12/min ⇒ ≥5s apart)
_BACKOFF_S = 20.0         # 429/503 backoff


def _cfg_of(prefs) -> dict:
    return dict(getattr(prefs, "notify_config", None) or {})


def _token(cfg: dict) -> str:
    return str(cfg.get("whatsappAgentKey") or "").strip()


def _headers(cfg: dict) -> dict:
    return {"Authorization": f"Bearer {_token(cfg)}"}


async def poll_updates(cfg: dict, offset: str | None, *, client: httpx.AsyncClient,
                       timeout_s: int = _POLL_TIMEOUT_S, limit: int = _POLL_LIMIT) -> dict:
    """One long-poll for updates. Returns the parsed JSON (``{entry:[…], next_offset}``) or ``{}`` on any
    error. On a cold start pass ``offset=None`` to start at the head and skip the 30-day backlog."""
    params: dict = {"timeout": timeout_s, "limit": limit}
    if offset is not None:
        params["offset"] = offset
    try:
        r = await client.get(f"{_BASE_URL}/updates", params=params, headers=_headers(cfg))
        if r.status_code == 200:
            return r.json() or {}
        log.warning("wa_agent.poll_status", status=r.status_code)
    except Exception as exc:  # noqa: BLE001 — a bad poll must never crash the loop
        log.warning("wa_agent.poll_error", error=str(exc))
    return {}


async def send_text(cfg: dict, to: str, body: str, *, context_id: str | None = None,
                    client: httpx.AsyncClient) -> str | None:
    """Send a text reply to ``to`` (must be ``user:<id>``). Returns the sent message id, or None. Applies
    the manual's retry rules: 2xx done; 429/503 back off + one retry; other 4xx not retried."""
    if not to.startswith("user:"):
        log.warning("wa_agent.bad_to")  # never leak the raw identifier into logs
        return None
    payload: dict = {"messaging_product": "whatsapp", "to": to, "type": "text",
                     "text": {"body": (body or "")[:_MAX_TEXT]}}
    if context_id:
        payload["context"] = {"message_id": context_id}
    for attempt in (1, 2):
        try:
            r = await client.post(f"{_BASE_URL}/messages", json=payload, headers=_headers(cfg))
            if 200 <= r.status_code < 300:
                data = r.json() or {}
                msgs = data.get("messages") or []
                return str(msgs[0]["id"]) if msgs and "id" in msgs[0] else ""
            if r.status_code in (429, 503) and attempt == 1:
                await asyncio.sleep(_BACKOFF_S)
                continue  # transient → one backoff retry
            log.warning("wa_agent.send_status", status=r.status_code)
            return None  # other 4xx/5xx → don't hammer
        except Exception as exc:  # noqa: BLE001
            log.warning("wa_agent.send_error", error=str(exc))
            return None
    return None


def _text_messages(updates: dict) -> list[dict]:
    """Flatten the updates envelope to the inbound TEXT messages (v1 handles text only)."""
    out: list[dict] = []
    for entry in updates.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            if change.get("field") != "messages":
                continue
            for msg in (change.get("value", {}) or {}).get("messages", []) or []:
                out.append(msg)
    return out


async def _persist_offset(store, cfg: dict, next_offset: str) -> None:
    """Persist the cursor in notify_config so restarts resume with no missed/replayed messages."""
    with contextlib.suppress(Exception):
        merged = dict(cfg)
        merged["whatsappAgentOffset"] = next_offset
        await store.update_settings(notify_config=merged)


async def poll_once(store, engine, *, client: httpx.AsyncClient) -> str | None:
    """One receive→route→reply→persist cycle. Returns the new offset (or None if inert/no change).
    Reused by the loop and by tests (with a mocked client)."""
    prefs = await store.get_settings()
    if not getattr(prefs, "notify_enabled", False):
        return None
    cfg = _cfg_of(prefs)
    if not _token(cfg):
        return None  # channel not configured — inert (Rule 0)
    offset = cfg.get("whatsappAgentOffset")
    updates = await poll_updates(cfg, offset, client=client)
    if not updates:
        return None
    last_send = 0.0
    for msg in _text_messages(updates):
        sender = str(msg.get("from") or "")
        if not sender.startswith("user:"):
            continue  # only converse with the creator's user:<id>
        body = str((msg.get("text") or {}).get("body") or "")
        if not body:
            with contextlib.suppress(Exception):
                await send_text(cfg, sender, "I can only read text messages for now.",
                                context_id=msg.get("id"), client=client)
            continue
        try:
            reply = await handle_stateful(body, sender, store, engine)
        except Exception as exc:  # noqa: BLE001 — isolate per message
            log.warning("wa_agent.handle_error", error=str(exc))
            continue
        # Self-throttle: no concurrent sends, ≥5s apart (≤12/min).
        wait = _SEND_MIN_INTERVAL - (asyncio.get_event_loop().time() - last_send)
        if wait > 0:
            await asyncio.sleep(wait)
        with contextlib.suppress(Exception):
            await send_text(cfg, sender, reply, context_id=msg.get("id"), client=client)
        last_send = asyncio.get_event_loop().time()
    next_offset = updates.get("next_offset")
    if next_offset is not None:
        await _persist_offset(store, cfg, str(next_offset))
        return str(next_offset)
    return None


async def run_forever(store, engine) -> None:
    """Background loop: long-poll → route → reply → persist offset. Inert until configured; never crashes
    the app. The long-poll itself paces the loop; a short sleep follows an empty/error cycle."""
    with contextlib.suppress(asyncio.CancelledError):
        async with httpx.AsyncClient(timeout=_READ_TIMEOUT_S) as client:
            while True:
                try:
                    changed = await poll_once(store, engine, client=client)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.warning("wa_agent.loop_error", error=str(exc))
                    changed = None
                if changed is None:
                    await asyncio.sleep(5.0)  # inert / idle / error → don't hot-spin
