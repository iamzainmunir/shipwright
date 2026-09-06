"""Slack **Socket Mode** client — two-way Slack (slash commands + Approve/Reject buttons) over an
outbound WebSocket, so it needs **no public URL / tunnel** and works behind a firewall.

Tokens live in the DB (``notify_config``), configured in the UI: an **app-level token** (``xapp-…``,
scope ``connections:write``) opens the socket; a **bot token** (``xoxb-…``, scope ``chat:write``)
posts the button-update replies. The loop is inert until both are set (Rule 0: offline-first) and
failure-isolated — a socket hiccup logs and retries, never crashing the app. The command/approval
logic is the same tested resolver the HTTP endpoints use (:mod:`app.slack_integration`).

Tokens are read when the socket (re)connects; change them → restart the orchestrator to pick up.
"""
from __future__ import annotations

import asyncio
import contextlib

import httpx
import structlog

from .notifier import CH_SLACK
from .slack_integration import resolve_action, resolve_command

log = structlog.get_logger(__name__)

_RETRY = 30.0  # seconds between connect attempts while unconfigured or after a failure


async def _tokens(store) -> tuple[str, str]:
    """The (app_token, bot_token) pair — empty unless notifications + the Slack channel are on."""
    try:
        prefs = await store.get_settings()
    except Exception:  # pragma: no cover - store hiccup must not crash the loop
        return "", ""
    if not getattr(prefs, "notify_enabled", False):
        return "", ""
    if not (getattr(prefs, "notify_channels", None) or {}).get(CH_SLACK, False):
        return "", ""
    cfg = getattr(prefs, "notify_config", None) or {}
    return str(cfg.get("slackAppToken") or "").strip(), str(cfg.get("slackBotToken") or "").strip()


async def _handle(client, req, store, engine) -> None:
    """Route one Socket Mode request → ack (+ reply). Never raises."""
    from slack_sdk.socket_mode.response import SocketModeResponse

    try:
        if req.type == "slash_commands":
            resp = await resolve_command((req.payload or {}).get("text", ""), store)
            await client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id, payload=resp))
            return

        if req.type == "interactive":
            await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
            result = await resolve_action(req.payload or {}, store, engine)
            url = (req.payload or {}).get("response_url")
            if url:  # update the original message in place
                async with httpx.AsyncClient(timeout=10.0) as http:
                    await http.post(url, json=result)
            return

        await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
    except Exception as exc:  # noqa: BLE001 — a handler error must never kill the socket
        log.warning("slack.socket_handle_error", type=getattr(req, "type", None), error=str(exc))
        with contextlib.suppress(Exception):
            await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))


async def run_forever(store, engine) -> None:
    """Keep a Socket Mode connection up whenever tokens are configured; reconnect on failure."""
    from slack_sdk.socket_mode.aiohttp import SocketModeClient
    from slack_sdk.web.async_client import AsyncWebClient

    with contextlib.suppress(asyncio.CancelledError):
        while True:
            app_token, bot_token = await _tokens(store)
            if not (app_token and bot_token):
                await asyncio.sleep(_RETRY)  # not configured yet — check again shortly
                continue

            client = SocketModeClient(app_token=app_token, web_client=AsyncWebClient(token=bot_token))
            client.socket_mode_request_listeners.append(
                lambda c, r: _handle(c, r, store, engine))  # slack awaits the returned coroutine
            try:
                await client.connect()
                log.info("slack.socket_connected")
                await asyncio.Event().wait()  # park; the client auto-reconnects internally
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await client.disconnect()
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("slack.socket_error", error=str(exc))
                with contextlib.suppress(Exception):
                    await client.disconnect()
                await asyncio.sleep(_RETRY)
