"""Stateful WhatsApp inbound bridge (P4) — glue between the transport (api/v1/whatsapp.py), the
per-sender session state machine (:mod:`app.wa_session`), the control executor (:mod:`app.wa_control`),
and the persistent session store.

Flow per inbound message:
  load/refresh session → parse_intent → advance → (execute action) → persist session → reply.

Everything is failure-isolated: a bad message or a store hiccup returns a safe reply, never a 500 —
the webhook must always answer the provider.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from foundry_core.ids import new_ulid
from foundry_core.models import WaSession

from . import wa_control, wa_session
from .seed import DEMO_ORG, DEMO_WS
from .whatsapp_integration import normalize_sender

log = structlog.get_logger(__name__)

_SESSION_TTL_S = 1800  # 30 min — a stale conversation resets to idle


async def _load_state(store, workspace_id: str, sender: str) -> tuple[wa_session.WaSessionState, str | None]:
    """Load the persisted session for a sender → a WaSessionState, or a fresh one. Returns the state and
    the existing row id (None if new). An expired session is treated as fresh (idle)."""
    getter = getattr(store, "get_wa_session", None)
    if getter is None:
        return wa_session.WaSessionState(sender=sender), None
    try:
        row = await getter(workspace_id, sender)
    except Exception:  # noqa: BLE001
        row = None
    if row is None:
        return wa_session.WaSessionState(sender=sender), None
    expired = row.expires_at is not None and row.expires_at < datetime.now(UTC)
    if expired:
        return wa_session.WaSessionState(sender=sender), row.id
    try:
        state = wa_session.WaState(str(row.state))
    except ValueError:
        state = wa_session.WaState.IDLE
    return wa_session.WaSessionState(sender=sender, state=state, context=dict(row.context or {})), row.id


async def _save_state(store, workspace_id: str, state: wa_session.WaSessionState, row_id: str | None) -> None:
    """Persist the (mutated) session state with a fresh TTL. Best-effort — never raises."""
    upsert = getattr(store, "upsert_wa_session", None)
    if upsert is None:
        return
    now = datetime.now(UTC)
    session = WaSession(
        id=row_id or new_ulid(), org_id=DEMO_ORG, workspace_id=workspace_id, sender=state.sender,
        state=str(getattr(state.state, "value", state.state)), context=dict(state.context or {}),
        updated_at=now, expires_at=now + timedelta(seconds=_SESSION_TTL_S),
    )
    try:
        await upsert(session)
    except Exception as exc:  # noqa: BLE001
        log.warning("wa.session_save_failed", error=str(exc))


async def handle_stateful(text: str, sender: str, store, engine, *, workspace_id: str = DEMO_WS) -> str:
    """Drive one inbound WhatsApp message through the conversational control plane and return the reply.
    Never raises."""
    try:
        key = normalize_sender(sender) or sender   # provider-independent session key
        state, row_id = await _load_state(store, workspace_id, key)
        intent = wa_session.parse_intent(text, state)
        reply, action = wa_session.advance(state, intent)
        if action is not None:
            reply = await wa_control.execute(
                action, store=store, engine=engine, actor=f"whatsapp:{sender}", workspace_id=workspace_id)
        await _save_state(store, workspace_id, state, row_id)
        return reply or "OK"
    except Exception as exc:  # noqa: BLE001 — the webhook must always answer
        log.warning("wa.handle_failed", error=str(exc))
        return "Sorry, something went wrong handling that."
