"""Shared inbound-command brain for the two-way channels (WhatsApp, Email, …).

One grammar, one resolver, so every channel behaves identically:
  * status queries (read-only) — ``status``, ``missions``, ``mission <KEY>``, ``tickets``, ``models``, ``help``
  * approvals — ``approve <MISSION-KEY>`` / ``reject <MISSION-KEY>`` resolves that mission's open gate

Slack is the exception: it carries the blocker id in its button payload, so it uses
:func:`app.slack_integration.resolve_action` directly rather than parsing free text.
"""
from __future__ import annotations

from foundry_core.enums import ApprovalDecision, BlockerKind

from .slack_integration import resolve_command

_APPROVE_WORDS = frozenset({"approve", "approved", "yes", "ok", "okay", "lgtm", "ship"})
_REJECT_WORDS = frozenset({"reject", "rejected", "no", "deny", "decline", "stop"})
_USAGE = "Send `status`, `missions`, `mission <KEY>`, or `approve/reject <KEY>`."


async def resolve_text(text: str, store, engine, *, actor: str = "inbound") -> str:
    """Parse an inbound message → a plain-text reply. ``actor`` attributes an approval to the
    channel it arrived on (e.g. "whatsapp", "email"). Never raises."""
    parts = (text or "").strip().split()
    if not parts:
        return _USAGE
    cmd = parts[0].lower()

    if cmd in _APPROVE_WORDS or cmd in _REJECT_WORDS:
        if len(parts) < 2:
            return "Which mission? Reply e.g. `approve SW-142`."
        approve = cmd in _APPROVE_WORDS
        return await _resolve_gate(parts[1], approve, store, engine, actor=actor)

    # status/missions/mission/tickets/models/help — reuse the shared resolver's text
    result = await resolve_command(text, store)
    return result.get("text", "OK")


async def _resolve_gate(key: str, approve: bool, store, engine, *, actor: str) -> str:
    mission = await store.get_mission(key)
    if mission is None:
        return f"Mission {key} not found."
    pending = [
        b for b in await store.list_blockers()
        if b.mission_id == mission.id and b.kind in (BlockerKind.APPROVAL, "approval")
    ]
    if not pending:
        return f"No pending approval on {key}."
    decision = ApprovalDecision.APPROVE if approve else ApprovalDecision.REJECT
    try:
        await engine.resolve_blocker(pending[0].id, decision, actor=actor, note=f"via {actor}")
    except Exception as exc:  # noqa: BLE001 — reply with the error, never crash the caller
        return f"Couldn't resolve {key}: {exc}"
    return f"{'✅ Approved' if approve else '🛑 Rejected'} {key}."
