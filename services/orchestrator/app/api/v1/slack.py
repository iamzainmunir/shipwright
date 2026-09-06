"""Slack inbound endpoints — slash commands (status queries) + interactivity (Approve/Reject
buttons). EVERY request is signature-verified against the DB-stored signing secret before we act.

Point your Slack app at:
  * Slash command request URL  →  POST /api/v1/integrations/slack/commands
  * Interactivity request URL  →  POST /api/v1/integrations/slack/interactivity
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, Response, status

from ...slack_integration import resolve_action, resolve_command, verify_slack
from ...state import get_engine, get_store

router = APIRouter()


async def _verify(request: Request) -> tuple[bool, dict]:
    """Verify Slack's request signature (using the DB signing secret) and return the parsed form."""
    raw = await request.body()
    prefs = await get_store().get_settings()
    secret = str((getattr(prefs, "notify_config", None) or {}).get("slackSigningSecret") or "").strip()
    ts = request.headers.get("X-Slack-Request-Timestamp", "")
    sig = request.headers.get("X-Slack-Signature", "")
    ok = verify_slack(secret, ts, sig, raw)
    form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
    return ok, form


@router.post("/integrations/slack/commands")
async def slack_commands(request: Request):
    ok, form = await _verify(request)
    if not ok:
        return Response(status_code=status.HTTP_401_UNAUTHORIZED, content="invalid signature")
    return await resolve_command(form.get("text", ""), get_store())


@router.post("/integrations/slack/interactivity")
async def slack_interactivity(request: Request):
    ok, form = await _verify(request)
    if not ok:
        return Response(status_code=status.HTTP_401_UNAUTHORIZED, content="invalid signature")
    try:
        payload = json.loads(form.get("payload", "{}"))
    except json.JSONDecodeError:
        return Response(status_code=status.HTTP_400_BAD_REQUEST, content="bad payload")
    return await resolve_action(payload, get_store(), get_engine())
