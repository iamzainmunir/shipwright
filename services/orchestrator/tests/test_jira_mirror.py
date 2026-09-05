"""Phase 6 — Jira mirror (plan 05 §4-6).

Covers the transactional-outbox enqueue seam (feature-gated, idempotent), and the OutboxWorker's
full error taxonomy against a fake Jira client — success + issue-map, rate-limit backoff (no attempt
increment), auth-parks-the-queue, 400-is-dead, and transient retry→exhaustion. No network: RULE 0
(Jira never touches the pipeline) is what makes this fully unit-testable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app import config
from app.jira_mirror import (
    JiraAuthError,
    JiraBadRequest,
    JiraConfig,
    JiraMirror,
    JiraRateLimited,
    JiraTransient,
    OutboxWorker,
)
from app.store import DEMO_WS, InMemoryStore
from app.tickets import TicketService
from foundry_core.enums import ArtifactKind
from foundry_core.ids import new_ulid
from foundry_core.models import JiraOutbox

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
CFG = JiraConfig(enabled=True, base_url="https://x.atlassian.net", email="a@b.c",
                 api_token="t", project_key="FND", max_attempts=3, base_delay_ms=1000)


class FakeJira:
    """In-memory JiraClient. `err` raises on every call (persistent); `raise_once` raises once."""

    def __init__(self) -> None:
        self.issues: dict[str, dict] = {}
        self.comments: list[tuple[str, str]] = []
        self.transitioned: list[tuple[str, str]] = []
        self.attachments: list[tuple[str, str]] = []
        self.links: list[tuple[str, str, str]] = []
        self.no_transition = False  # when True, transition() reports no matching workflow edge
        self.err: Exception | None = None
        self.raise_once: Exception | None = None
        self._seq = 0

    def _guard(self) -> None:
        if self.raise_once is not None:
            exc, self.raise_once = self.raise_once, None
            raise exc
        if self.err is not None:
            raise self.err

    def myself(self) -> dict:
        self._guard()
        return {"displayName": "Test User"}

    def create_issue(self, *, project_key, summary, issue_type, description, labels, parent_key=None):
        self._guard()
        self._seq += 1
        key = f"{project_key}-{self._seq}"
        self.issues[key] = {"summary": summary, "type": issue_type, "parent": parent_key,
                            "labels": labels, "description": description}
        return key

    def add_comment(self, key, body):
        self._guard()
        self.comments.append((key, body))

    def transition(self, key, target_names):
        self._guard()
        if self.no_transition or not target_names:
            return False
        self.transitioned.append((key, target_names[0]))
        return True

    def add_attachment(self, key, file_path):
        self._guard()
        self.attachments.append((key, file_path))

    def create_link(self, from_key, to_key, link_type):
        self._guard()
        self.links.append((from_key, to_key, link_type))


@pytest.fixture
async def wired():
    """Store with the Jira feature ON, a TicketService whose sink is the mirror, and a mission."""
    store = InMemoryStore()
    await store.update_settings(DEMO_WS, features={"tickets": True, "jira": True})
    mirror = JiraMirror(store)
    svc = TicketService(store, sink=mirror.on_ticket_event)
    mission = await store.get_mission("FND-142")
    return store, svc, mission


BUILD_FACTS = {
    "branch": "parallel/fnd-142", "files": ["api.py"],
    "subtasks": [{"subtask_id": "api", "title": "Build the API", "role": "backend",
                  "agent_name": "Ada", "files": ["api.py"]}],
}


async def _one_row(store, op="create", payload=None, ticket_id="tk1", key="ev1") -> JiraOutbox:
    return await store.add_jira_outbox(JiraOutbox(
        id=new_ulid(), workspace_id=DEMO_WS, idempotency_key=key, ticket_id=ticket_id, op=op,
        payload=payload or {"kind": "story", "title": "T", "description": {"goal": "g"}, "labels": []},
        status="pending", created_at=NOW, updated_at=NOW))


# ---- enqueue seam --------------------------------------------------------------

async def test_enqueue_only_when_feature_on(wired):
    store, svc, mission = wired
    await svc.sync_build(mission, BUILD_FACTS)
    rows = await store.list_jira_outbox(DEMO_WS)
    assert rows, "epic+story create/transition should have enqueued outbox rows"
    ops = [r.op for r in rows]
    assert "create" in ops and "transition" in ops
    # A rebuild does not duplicate the create ops (Epic/Story issues are created once); it may add a
    # fresh transition event, which is correct — each build is a real state change.
    creates_before = sum(1 for r in rows if r.op == "create")
    await svc.sync_build(mission, BUILD_FACTS)
    rows2 = await store.list_jira_outbox(DEMO_WS)
    assert sum(1 for r in rows2 if r.op == "create") == creates_before


async def test_no_enqueue_when_feature_off():
    store = InMemoryStore()
    await store.update_settings(DEMO_WS, features={"tickets": True, "jira": False})
    svc = TicketService(store, sink=JiraMirror(store).on_ticket_event)
    mission = await store.get_mission("FND-142")
    await svc.sync_build(mission, BUILD_FACTS)
    assert await store.list_jira_outbox(DEMO_WS) == []


# ---- worker happy path ---------------------------------------------------------

async def test_worker_creates_issues_maps_and_transitions(wired):
    store, svc, mission = wired
    await svc.sync_build(mission, BUILD_FACTS)
    await svc.on_qa(mission, "run1", passed=True, reason="looks good", evidence={})
    client = FakeJira()
    stats = await OutboxWorker(store).drain_once(DEMO_WS, client, CFG, now=NOW)
    assert stats.dead == 0 and stats.processed == stats.done
    # An Epic and a Story issue were created and mapped (map is keyed by the ticket id).
    assert len(client.issues) == 2
    epic = next(t for t in await store.list_tickets(mission_id=mission.id) if str(t.kind) == "epic")
    epic_map = await store.get_jira_issue_map(DEMO_WS, "epic", epic.id)
    assert epic_map is not None and epic_map.jira_key in client.issues
    # The Story reached In Review via real transitions.
    assert any(name for _k, name in client.transitioned)
    # All rows are done; a second drain has nothing to do (idempotent).
    assert not await store.list_jira_outbox(DEMO_WS, status="pending", due_at=NOW)
    stats2 = await OutboxWorker(store).drain_once(DEMO_WS, client, CFG, now=NOW)
    assert stats2.processed == 0


async def test_worker_attaches_evidence(wired, tmp_path, monkeypatch):
    store, svc, mission = wired
    monkeypatch.setenv("FOUNDRY_ARTIFACTS_ROOT", str(tmp_path))
    config.get_settings.cache_clear()
    from app.artifacts import make_artifact_record, run_artifact_dir
    d = run_artifact_dir(mission.id, "run1", "qa")
    shot = d / "screenshots" / "01_home.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    shot.write_bytes(b"\x89PNG\r\n fake bytes")
    art = make_artifact_record(workspace_id=DEMO_WS, mission_id=mission.id, run_id="run1",
                               file_path=shot, kind=ArtifactKind.SCREENSHOT)
    await store.add_artifact(art)

    await svc.sync_build(mission, BUILD_FACTS)
    await svc.on_qa(mission, "run1", passed=True, reason="ok",
                    evidence={"screenshots": [art.id], "summary": "rung=L0"})
    client = FakeJira()
    await OutboxWorker(store).drain_once(DEMO_WS, client, CFG, now=NOW)
    assert client.attachments and client.attachments[0][1].endswith("01_home.png")
    config.get_settings.cache_clear()


async def test_worker_links_bug_to_story(wired):
    store, svc, mission = wired
    await svc.sync_build(mission, BUILD_FACTS)
    await svc.on_qa(mission, "run2", passed=False, reason="button missing", evidence={})
    client = FakeJira()
    await OutboxWorker(store).drain_once(DEMO_WS, client, CFG, now=NOW)
    # Epic + Story + Bug created, and the Bug is linked to the Story.
    assert len(client.issues) == 3
    assert client.links and client.links[0][2] == CFG.link_type


# ---- worker error taxonomy -----------------------------------------------------

async def test_rate_limited_backs_off_without_counting_attempt():
    store = InMemoryStore()
    await _one_row(store)
    client = FakeJira()
    client.err = JiraRateLimited(30.0)
    stats = await OutboxWorker(store).drain_once(DEMO_WS, client, CFG, now=NOW)
    assert stats.rate_limited and stats.done == 0
    got = (await store.list_jira_outbox(DEMO_WS))[0]
    assert got.attempts == 0  # a 429 is NOT counted as an attempt
    assert got.next_attempt_at == NOW + timedelta(seconds=31)  # Retry-After + 1s
    assert str(got.status) == "pending"


async def test_auth_error_parks_the_workspace():
    store = InMemoryStore()
    await _one_row(store)
    client = FakeJira()
    client.err = JiraAuthError("401")
    stats = await OutboxWorker(store).drain_once(DEMO_WS, client, CFG, now=NOW)
    assert stats.parked and stats.done == 0
    # row is left pending (nothing lost); the jira integration is flagged for the UI
    assert str((await store.list_jira_outbox(DEMO_WS))[0].status) == "pending"
    jira = next((i for i in await store.list_integrations(DEMO_WS) if str(i.kind) == "jira"), None)
    if jira is not None:  # seeded integrations include jira
        assert str(jira.status) == "error"


async def test_bad_request_marks_row_dead():
    store = InMemoryStore()
    await _one_row(store)
    client = FakeJira()
    client.raise_once = JiraBadRequest("invalid field")
    stats = await OutboxWorker(store).drain_once(DEMO_WS, client, CFG, now=NOW)
    assert stats.dead == 1
    assert str((await store.list_jira_outbox(DEMO_WS))[0].status) == "dead"


async def test_transient_retries_then_dies():
    store = InMemoryStore()
    await _one_row(store)
    client = FakeJira()
    client.err = JiraTransient("503")
    worker = OutboxWorker(store)
    # Attempt 1 and 2 → retry with growing backoff; attempt 3 hits max_attempts → dead.
    now = NOW
    for expected_attempts in (1, 2):
        stats = await worker.drain_once(DEMO_WS, client, CFG, now=now)
        assert stats.retried == 1
        row = (await store.list_jira_outbox(DEMO_WS))[0]
        assert row.attempts == expected_attempts
        assert row.next_attempt_at == now + timedelta(
            milliseconds=CFG.base_delay_ms * (2 ** (expected_attempts - 1)))
        now = row.next_attempt_at  # advance past the backoff so the row is due again
    stats = await worker.drain_once(DEMO_WS, client, CFG, now=now)
    assert stats.dead == 1
    assert str((await store.list_jira_outbox(DEMO_WS))[0].status) == "dead"


async def test_idempotent_create_skips_when_already_mapped():
    store = InMemoryStore()
    row = await _one_row(store, payload={"kind": "story", "title": "T", "description": {}, "labels": []})
    from foundry_core.models import JiraIssueMap
    await store.add_jira_issue_map(JiraIssueMap(
        id=new_ulid(), workspace_id=DEMO_WS, entity_type="story", entity_id=row.ticket_id,
        jira_key="FND-999", created_at=NOW))
    client = FakeJira()
    stats = await OutboxWorker(store).drain_once(DEMO_WS, client, CFG, now=NOW)
    assert stats.done == 1 and client.issues == {}  # no duplicate create
