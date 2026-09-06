"""Slack two-way integration — inbound slash commands (status queries) and interactive
Approve/Reject buttons (gate approvals).

**Security-critical:** every inbound request is verified with Slack's request signing (HMAC-SHA256
over ``v0:{timestamp}:{body}``) BEFORE we act on it, with a 5-minute freshness window to stop
replays. The Slack *signing secret* and *webhook* live in the DB (``AutonomyPolicy.notify_config``),
configured in the UI — never in ``.env``.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from foundry_core.enums import ApprovalDecision

_MAX_SKEW = 60 * 5  # reject requests older than 5 minutes (replay protection)


def _v(x: object) -> str:
    return getattr(x, "value", None) or str(x)


def verify_slack(signing_secret: str, timestamp: str, signature: str, raw_body: bytes) -> bool:
    """True iff this is a genuine, fresh Slack request (signing scheme v0)."""
    if not (signing_secret and timestamp and signature):
        return False
    try:
        if abs(time.time() - int(timestamp)) > _MAX_SKEW:
            return False
    except (TypeError, ValueError):
        return False
    base = b"v0:" + timestamp.encode() + b":" + raw_body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _ephemeral(text: str) -> dict:
    return {"response_type": "ephemeral", "text": text}


async def resolve_command(text: str, store) -> dict:
    """Answer a ``/shipwright <cmd>`` status query (read-only) as a Slack response dict."""
    parts = (text or "").strip().split()
    cmd = parts[0].lower() if parts else "status"

    if cmd == "status":
        missions = await store.list_missions()
        blockers = await store.list_blockers()
        by_stage: dict[str, int] = {}
        for m in missions:
            by_stage[_v(m.stage)] = by_stage.get(_v(m.stage), 0) + 1
        in_flight = sum(v for k, v in by_stage.items() if k in ("spec", "building", "qa", "review"))
        shipped = by_stage.get("shipped", 0)
        stages = ", ".join(f"{k} {v}" for k, v in sorted(by_stage.items()))
        return _ephemeral(
            f"*Shipwright status*\n"
            f"• {len(missions)} missions — {in_flight} in flight, {shipped} shipped\n"
            f"• {len(blockers)} open blocker(s)"
            + (f"\n• stages: {stages}" if stages else "")
        )

    if cmd == "missions":
        missions = await store.list_missions()
        if not missions:
            return _ephemeral("No missions yet.")
        lines = [f"• *{m.key}* — {m.title} _({_v(m.stage)})_" for m in missions[:20]]
        return _ephemeral("*Missions*\n" + "\n".join(lines))

    if cmd == "mission" and len(parts) > 1:
        m = await store.get_mission(parts[1])
        if m is None:
            return _ephemeral(f"Mission {parts[1]} not found.")
        blockers = [b for b in await store.list_blockers() if b.mission_id == m.id]
        bl = f" · {len(blockers)} open blocker(s)" if blockers else ""
        return _ephemeral(f"*{m.key}* — {m.title}\nStage: {_v(m.stage)} · {m.progress}%{bl}")

    if cmd == "tickets":
        tickets = await store.list_tickets()
        cols: dict[str, int] = {}
        for t in tickets:
            cols[_v(t.status)] = cols.get(_v(t.status), 0) + 1
        if not cols:
            return _ephemeral("No tickets yet.")
        return _ephemeral("*Tickets*\n" + "\n".join(f"• {k}: {v}" for k, v in sorted(cols.items())))

    if cmd == "models":
        conns = await store.list_model_connections()
        if not conns:
            return _ephemeral("No models connected.")
        lines = [f"• {_v(c.provider)}{' _(primary)_' if getattr(c, 'is_primary', False) else ''}"
                 for c in conns]
        return _ephemeral("*Models*\n" + "\n".join(lines))

    return _ephemeral(
        "*Shipwright commands*\n"
        "• `/shipwright status` — org overview\n"
        "• `/shipwright missions` — list missions\n"
        "• `/shipwright mission <KEY>` — one mission\n"
        "• `/shipwright tickets` — ticket-board counts\n"
        "• `/shipwright models` — connected models"
    )


async def resolve_action(payload: dict, store, engine) -> dict:
    """Handle an Approve/Reject button click → resolve the blocker. Returns a Slack message dict."""
    actions = payload.get("actions") or []
    if not actions:
        return {"text": "No action."}
    value = str(actions[0].get("value") or "")
    try:
        decision_str, blocker_id = value.split(":", 1)
    except ValueError:
        return {"text": "Malformed action."}
    user = (payload.get("user") or {}).get("username") or "slack"

    blocker = await store.get_blocker(blocker_id)
    if blocker is None:
        return {"replace_original": True, "text": "That approval no longer exists."}
    if blocker.resolved_at is not None:
        return {"replace_original": True, "text": "That gate was already resolved."}

    decision = ApprovalDecision.APPROVE if decision_str == "approve" else ApprovalDecision.REJECT
    try:
        await engine.resolve_blocker(blocker_id, decision, actor=f"slack:{user}", note="via Slack")
    except Exception as exc:  # noqa: BLE001 — surface the failure in Slack, never 500 the callback
        return {"replace_original": True, "text": f"Couldn't resolve: {exc}"}
    verb = "✅ Approved" if decision == ApprovalDecision.APPROVE else "🛑 Rejected"
    return {"replace_original": True, "text": f"{verb} by @{user}."}


def approval_blocks(subject: str, body: str, blocker_id: str) -> list[dict]:
    """Block Kit for an approval alert — Approve / Reject buttons carrying the blocker id."""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{subject}*\n{body}"}},
        {"type": "actions", "elements": [
            {"type": "button", "style": "primary", "action_id": "sw_approve",
             "text": {"type": "plain_text", "text": "Approve"}, "value": f"approve:{blocker_id}"},
            {"type": "button", "style": "danger", "action_id": "sw_reject",
             "text": {"type": "plain_text", "text": "Reject"}, "value": f"reject:{blocker_id}"},
        ]},
    ]
