"""WhatsApp 'mission <KEY>' and 'status' must report accurate, at-a-glance progress — the mission's
stage + percent AND its ticket breakdown (how many To Do / In Progress / QA / Done) — so the owner can
run the org from chat. Failure-isolated: a store without tickets still returns the base line."""
from __future__ import annotations

from app import wa_control
from app.seed import DEMO_WS
from app.store import InMemoryStore
from foundry_core.enums import TicketKind, TicketStatus
from foundry_core.ids import new_ulid
from foundry_core.models import Ticket


async def _add_ticket(store: InMemoryStore, mission_id: str, status: TicketStatus,
                      kind: TicketKind = TicketKind.STORY) -> None:
    await store.add_ticket(Ticket(
        id=new_ulid(), workspace_id=DEMO_WS, key=await store.next_ticket_key(),
        kind=kind, mission_id=mission_id, title="t", status=status,
    ))


async def test_mission_command_includes_progress_and_ticket_breakdown():
    store = InMemoryStore()
    mission = await store.get_mission("FND-142")  # seeded, no tickets
    await _add_ticket(store, mission.id, TicketStatus.TODO)
    await _add_ticket(store, mission.id, TicketStatus.TODO)
    await _add_ticket(store, mission.id, TicketStatus.IN_PROGRESS)
    await _add_ticket(store, mission.id, TicketStatus.QA)
    await _add_ticket(store, mission.id, TicketStatus.DONE)

    reply = (await wa_control._do_mission({"mission_key": mission.key}, store=store)).lower()

    assert mission.key.lower() in reply           # the mission key
    assert "2 to do" in reply                      # counts by board column
    assert "1 in progress" in reply
    assert "1 qa" in reply
    assert "1 done" in reply


async def test_mission_command_without_tickets_still_returns_base_line():
    store = InMemoryStore()
    mission = await store.get_mission("FND-150")
    reply = await wa_control._do_mission({"mission_key": mission.key}, store=store)
    assert mission.key in reply  # no tickets → no breakdown, but the base status line still returns


async def test_status_includes_aggregate_ticket_summary():
    store = InMemoryStore()
    mission = await store.get_mission("FND-142")
    await _add_ticket(store, mission.id, TicketStatus.TODO)
    await _add_ticket(store, mission.id, TicketStatus.DONE)
    reply = (await wa_control._do_status(store=store, workspace_id=DEMO_WS)).lower()
    assert "ticket" in reply and "1 to do" in reply and "1 done" in reply
