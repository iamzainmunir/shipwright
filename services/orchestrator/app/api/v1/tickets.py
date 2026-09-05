"""Built-in ticket board API (v2 Phase 5 — plan 05 §3).

Read endpoints for the dashboard Tickets board + drawer, and the Settings → Features toggle. The
board derives entirely from the ticket rows the engine's consumer writes; nothing here mutates the
pipeline. Enabling the board runs a one-time backfill so it is never mysteriously empty.

Canonical paths:
  * GET  /api/v1/tickets                    board list (optionally ?missionId=)     missions:read
  * GET  /api/v1/tickets/{id}               ticket + activity feed + links          missions:read
  * GET  /api/v1/missions/{key}/tickets     a mission's Epic + Stories + Bugs        missions:read
  * GET  /api/v1/features                   feature toggles (tickets, jira)          missions:read
  * PUT  /api/v1/features                   flip a feature toggle                    settings:write
"""

from __future__ import annotations

import contextlib

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from ...errors import not_found
from ...seed import DEMO_WS
from ...state import get_engine, get_store

router = APIRouter(tags=["tickets"])


class _Body(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class FeaturesBody(_Body):
    tickets: bool | None = None
    jira: bool | None = None


@router.get("/tickets", description="x-required-scope: missions:read")
async def list_tickets(missionId: str | None = None) -> list[dict]:  # noqa: N803 (query alias)
    """All board tickets for the workspace, oldest→newest. ``missionId`` narrows to one Epic's tree."""
    store = get_store()
    mission_id = None
    if missionId:
        m = await store.get_mission(missionId)  # accept a mission key or id
        mission_id = m.id if m else missionId
    tickets = await store.list_tickets(mission_id=mission_id, workspace_id=DEMO_WS)
    return [t.model_dump(by_alias=True) for t in tickets]


@router.get("/tickets/{ticket_id}", description="x-required-scope: missions:read")
async def get_ticket(ticket_id: str) -> dict:
    """A ticket with its full activity feed and links — the drawer payload."""
    store = get_store()
    ticket = await store.get_ticket(ticket_id)
    if ticket is None:
        raise not_found(f"ticket {ticket_id} not found")
    events = await store.list_ticket_events(ticket_id)
    links = await store.list_ticket_links(ticket_id)
    return {
        "ticket": ticket.model_dump(by_alias=True),
        "events": [e.model_dump(by_alias=True) for e in events],
        "links": [ln.model_dump(by_alias=True) for ln in links],
    }


@router.get("/missions/{key}/tickets", description="x-required-scope: missions:read")
async def list_mission_tickets(key: str) -> list[dict]:
    store = get_store()
    mission = await store.get_mission(key)
    if mission is None:
        raise not_found(f"mission {key} not found")
    tickets = await store.list_tickets(mission_id=mission.id, workspace_id=DEMO_WS)
    return [t.model_dump(by_alias=True) for t in tickets]


@router.get("/features", description="x-required-scope: missions:read")
async def get_features() -> dict:
    """Effective product-feature toggles for the workspace (Settings → Features)."""
    from ...config import get_settings
    store = get_store()
    policy = await store.get_settings(DEMO_WS)
    defaults = {"tickets": get_settings().tickets_enabled, "jira": False}
    return {**defaults, **(policy.features or {})}


@router.put("/features", description="x-required-scope: settings:write")
async def update_features(body: FeaturesBody) -> dict:
    """Flip a feature toggle. Turning the ticket board ON runs a one-time backfill so existing
    missions get their Epics immediately."""
    from ...config import get_settings
    store = get_store()
    policy = await store.get_settings(DEMO_WS)
    features = dict(policy.features or {})
    was_on = features.get("tickets", get_settings().tickets_enabled)
    if body.tickets is not None:
        features["tickets"] = body.tickets
    if body.jira is not None:
        features["jira"] = body.jira
    await store.update_settings(DEMO_WS, features=features)
    # Reconcile on enable: backfill Epics for missions that predate the toggle.
    if body.tickets and not was_on:
        with contextlib.suppress(Exception):
            await get_engine()._tickets.backfill(DEMO_WS)
    defaults = {"tickets": get_settings().tickets_enabled, "jira": False}
    return {**defaults, **features}
