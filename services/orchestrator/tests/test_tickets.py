"""Phase 5 — built-in ticket board (plan 05).

Covers the consumer that derives Epics/Stories/Bugs from the lifecycle (idempotency, transitions,
who/what/why on every event, reopen/bug-link semantics, the Settings toggle, and backfill), the
engine seam (start_run creates the Epic), and the read API + features toggle over TestClient.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from app.store import DEMO_WS, InMemoryStore
from app.tickets import TicketService


@pytest.fixture
async def wiring():
    store = InMemoryStore()
    svc = TicketService(store)
    mission = await store.get_mission("FND-142")
    assert mission is not None
    return store, svc, mission


def _kinds(tickets, kind):
    return [t for t in tickets if str(t.kind) == kind]


BUILD_FACTS = {
    "branch": "parallel/fnd-142",
    "files": ["api.py", "ui.js"],
    "subtasks": [
        {"subtask_id": "api", "title": "Build the API", "role": "backend",
         "agent_name": "Ada", "files": ["api.py"]},
        {"subtask_id": "ui", "title": "Build the UI", "role": "frontend",
         "agent_name": "Ivy", "files": ["ui.js"]},
    ],
}


# ---- consumer ------------------------------------------------------------------

async def test_ensure_epic_is_idempotent(wiring):
    store, svc, mission = wiring
    e1 = await svc.ensure_epic(mission)
    e2 = await svc.ensure_epic(mission)
    assert e1 is not None and e1.id == e2.id
    assert str(e1.kind) == "epic"
    assert e1.key.startswith("FT-")
    epics = _kinds(await store.list_tickets(mission_id=mission.id), "epic")
    assert len(epics) == 1
    # creation always logs who/what/why
    evs = await store.list_ticket_events(e1.id)
    assert any(str(ev.kind) == "created" and ev.actor_name for ev in evs)


async def test_sync_build_creates_stories_and_moves_to_in_review(wiring):
    # Pipeline order is build → review → QA → ship, so a finished build moves stories to In Review
    # (code review) — not straight to QA.
    store, svc, mission = wiring
    await svc.sync_build(mission, BUILD_FACTS)
    stories = _kinds(await store.list_tickets(mission_id=mission.id), "story")
    assert len(stories) == 2
    assert {s.agent_name for s in stories} == {"Ada", "Ivy"}
    assert all(str(s.status) == "in_review" for s in stories)
    # each Story records CREATED then a TRANSITIONED→in_review with a comment (who/what/why)
    evs = await store.list_ticket_events(stories[0].id)
    assert any(str(e.kind) == "created" for e in evs)
    to_rev = [e for e in evs if str(e.kind) == "transitioned" and str(e.to_status) == "in_review"]
    assert to_rev and to_rev[0].body.get("reason")
    # idempotent: a second build doesn't duplicate stories
    await svc.sync_build(mission, BUILD_FACTS)
    assert len(_kinds(await store.list_tickets(mission_id=mission.id), "story")) == 2


async def test_review_approve_then_qa_pass_keeps_stories_in_qa_with_evidence(wiring):
    # build → In Review; review approves → QA; QA passes → stays in QA (final gate) with evidence.
    store, svc, mission = wiring
    await svc.sync_build(mission, BUILD_FACTS)
    await svc.on_reviewed(mission, approved=True, reason="looks good")
    assert all(str(s.status) == "qa"
               for s in _kinds(await store.list_tickets(mission_id=mission.id), "story"))
    await svc.on_qa(mission, "run1", passed=True, reason="acceptance met",
                    evidence={"screenshots": ["art-1", "art-2"], "summary": "rung=L0"})
    stories = _kinds(await store.list_tickets(mission_id=mission.id), "story")
    assert all(str(s.status) == "qa" for s in stories)  # QA is the final gate → ship moves it to Done
    evs = await store.list_ticket_events(stories[0].id)
    attached = [e for e in evs if str(e.kind) == "evidence_attached"]
    assert attached and attached[0].body.get("artifact_ids") == ["art-1", "art-2"]


async def test_qa_fail_opens_bug_blocks_story_and_reopens(wiring):
    store, svc, mission = wiring
    await svc.sync_build(mission, BUILD_FACTS)
    await svc.on_qa(mission, "run2", passed=False, reason="Add-task button missing", evidence={})
    tickets = await store.list_tickets(mission_id=mission.id)
    bugs = _kinds(tickets, "bug")
    assert len(bugs) == 1 and bugs[0].run_id == "run2"
    # bug blocks a story
    links = await store.list_ticket_links(bugs[0].id)
    assert links and str(links[0].link_type) == "blocks"
    # a story is reopened with an incremented reopen count
    reopened = [s for s in _kinds(tickets, "story") if str(s.status) == "reopened"]
    assert reopened and reopened[0].reopen_count == 1
    # idempotent: same run doesn't create a second bug
    await svc.on_qa(mission, "run2", passed=False, reason="still missing", evidence={})
    assert len(_kinds(await store.list_tickets(mission_id=mission.id), "bug")) == 1


async def test_shipped_closes_epic_and_stories(wiring):
    store, svc, mission = wiring
    await svc.sync_build(mission, BUILD_FACTS)
    await svc.on_shipped(mission)
    tickets = await store.list_tickets(mission_id=mission.id)
    assert all(str(t.status) == "done" for t in tickets if str(t.kind) in ("epic", "story"))
    epic = _kinds(tickets, "epic")[0]
    assert any(str(e.kind) == "shipped" for e in await store.list_ticket_events(epic.id))


async def test_change_request_reopens_epic_and_stories(wiring):
    store, svc, mission = wiring
    await svc.sync_build(mission, BUILD_FACTS)
    await svc.on_shipped(mission)
    await svc.on_change_request(mission, "Add a dark mode toggle")
    tickets = await store.list_tickets(mission_id=mission.id)
    assert str(_kinds(tickets, "epic")[0].status) == "reopened"
    assert all(str(s.status) == "reopened" for s in _kinds(tickets, "story"))


async def test_disabled_toggle_writes_nothing(wiring):
    store, svc, mission = wiring
    await store.update_settings(DEMO_WS, features={"tickets": False})
    assert await svc.ensure_epic(mission) is None
    await svc.sync_build(mission, BUILD_FACTS)
    assert await store.list_tickets(mission_id=mission.id) == []


async def test_backfill_creates_one_epic_per_mission():
    store = InMemoryStore()
    svc = TicketService(store)
    n = await svc.backfill(DEMO_WS)
    missions = await store.list_missions(DEMO_WS)
    assert n == len(missions) and n >= 1
    # running it again is a no-op (idempotent)
    assert await svc.backfill(DEMO_WS) == 0


async def test_backfill_derives_epic_status_from_mission_stage():
    """Regression: backfill must NOT hard-set every Epic to in_progress — a shipped mission's Epic is
    Done, a stopped mission's is Blocked, and only mid-pipeline ones are In Progress."""
    store = InMemoryStore()
    svc = TicketService(store)
    ships = await store.get_mission("FND-142")
    await store.update_mission(ships.id, stage="shipped")
    # pick a different mission to force to stopped
    others = [m for m in await store.list_missions(DEMO_WS) if m.id != ships.id]
    stopped = others[0]
    await store.update_mission(stopped.id, stage="stopped")

    await svc.backfill(DEMO_WS)
    ep_ship = await store.find_ticket(mission_id=ships.id, kind="epic")
    ep_stop = await store.find_ticket(mission_id=stopped.id, kind="epic")
    assert str(ep_ship.status) == "done"
    assert str(ep_stop.status) == "blocked"


async def test_backfill_reconciles_a_drifted_epic():
    """The exact bug the user hit: an Epic left In Progress while its mission is shipped gets
    corrected the next time the board reconciles (backfill)."""
    store = InMemoryStore()
    svc = TicketService(store)
    mission = await store.get_mission("FND-142")
    epic = await svc.ensure_epic(mission)  # created In Progress (live default)
    assert str(epic.status) == "in_progress"
    await store.update_mission(mission.id, stage="shipped")
    await svc.backfill(DEMO_WS)
    assert str((await store.get_ticket(epic.id)).status) == "done"


async def test_reconcile_never_clobbers_a_mid_pipeline_epic():
    """Reconcile only settles Epics to Done/Blocked for shipped/stopped missions — a mission still
    mid-pipeline keeps whatever status the live consumer set (never forced backward/forward)."""
    store = InMemoryStore()
    svc = TicketService(store)
    mission = await store.get_mission("FND-142")
    await store.update_mission(mission.id, stage="building")
    epic = await svc.ensure_epic(mission)  # In Progress
    await svc.backfill(DEMO_WS)
    assert str((await store.get_ticket(epic.id)).status) == "in_progress"


async def test_cancel_blocks_epic_and_open_stories_then_rerun_unblocks(wiring):
    store, svc, mission = wiring
    await svc.sync_build(mission, BUILD_FACTS)  # stories → qa, epic in_progress
    stopped = await store.update_mission(mission.id, stage="stopped")
    await svc.on_cancelled(stopped)

    tickets = await store.list_tickets(mission_id=mission.id)
    assert str(_kinds(tickets, "epic")[0].status) == "blocked"
    assert all(str(s.status) == "blocked" for s in _kinds(tickets, "story"))

    # A retry lifts the Blocked Epic back to active (In Progress).
    await svc.on_run_started(mission)
    assert str((await store.find_ticket(mission_id=mission.id, kind="epic")).status) == "in_progress"


async def test_shipped_epic_is_not_reblocked_by_cancel(wiring):
    # A Done Epic must never be dragged back to Blocked by a stray cancel (e.g. superseded run).
    store, svc, mission = wiring
    await svc.sync_build(mission, BUILD_FACTS)
    await svc.on_shipped(mission)
    await svc.on_cancelled(mission)
    assert str((await store.find_ticket(mission_id=mission.id, kind="epic")).status) == "done"


# ---- engine seam ---------------------------------------------------------------

async def test_start_run_creates_epic():
    from app.engine import RunEngine
    from app.events import EventBus

    from tests.support.scripted_provider import ScriptedProvider

    store = InMemoryStore()
    store.model_connections.clear()
    engine = RunEngine(store, EventBus(), ScriptedProvider())
    mission = await store.get_mission("FND-142")
    run = await engine.start_run(mission)
    # The Epic is created synchronously inside start_run, before the run task is scheduled.
    epics = _kinds(await store.list_tickets(mission_id=mission.id), "epic")
    assert len(epics) == 1
    # Tidy up the background run task so it doesn't dangle past the test.
    task = engine._run_tasks.get(run.id)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


# ---- read API + features toggle ------------------------------------------------

def test_features_and_board_api():
    from app.main import create_app
    from fastapi.testclient import TestClient

    client = TestClient(create_app())

    feats = client.get("/api/v1/features").json()
    assert feats["tickets"] is True and feats["jira"] is False

    # Toggle off then on → the enable path backfills Epics for seeded missions.
    assert client.put("/api/v1/features", json={"tickets": False}).json()["tickets"] is False
    assert client.put("/api/v1/features", json={"tickets": True}).json()["tickets"] is True

    rows = client.get("/api/v1/tickets").json()
    assert isinstance(rows, list) and rows
    assert any(t["kind"] == "epic" for t in rows)
    # camelCase wire contract
    assert "workspaceId" in rows[0] and "workspace_id" not in rows[0]

    # Drawer payload: ticket + activity feed + links.
    detail = client.get(f"/api/v1/tickets/{rows[0]['id']}").json()
    assert set(detail) == {"ticket", "events", "links"}
    assert detail["ticket"]["id"] == rows[0]["id"]

    assert client.get("/api/v1/tickets/nope").status_code == 404


async def test_delete_mission_cascades_tickets_events_and_links(wiring):
    """Deleting a mission must take its whole ticket trail with it — otherwise the board (and the
    sidebar count) keeps showing orphaned tickets for a project that no longer exists. Regression for
    the 'deleted all missions but tickets still there' bug."""
    from foundry_core.models import TicketLink

    store, svc, mission = wiring
    await svc.ensure_epic(mission)
    await svc.sync_build(mission, BUILD_FACTS)  # creates stories + their created/transition events

    tickets = await store.list_tickets(mission_id=mission.id)
    assert len(tickets) >= 2  # epic + at least one story
    # A link between two of this mission's tickets must be swept too.
    await store.add_ticket_link(TicketLink(
        id="tl-cascade-test", workspace_id=mission.workspace_id,
        from_ticket=tickets[0].id, to_ticket=tickets[1].id,
    ))
    assert await store.list_ticket_events(tickets[1].id)  # trail exists before delete

    await store.delete_mission(mission.id)

    assert await store.list_tickets(mission_id=mission.id) == []
    assert await store.get_mission(mission.id) is None
    for t in tickets:
        assert await store.get_ticket(t.id) is None
        assert await store.list_ticket_events(t.id) == []
        assert await store.list_ticket_links(t.id) == []
