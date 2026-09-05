"""PM planning phase — upfront tickets (with dependencies) + status-on-build-start.

Guards the user-reported bugs: (1) the PM must break a project into tickets BEFORE building, with
dependency links and skill/role assignment; (2) a story must show In Progress WHILE the engineer
builds (not appear already in QA); (3) the plan/build must line up so no planned story is orphaned.
"""

from __future__ import annotations

from app import devloop
from app.store import InMemoryStore
from app.tickets import TicketService
from foundry_core.enums import TicketKind, TicketStatus


async def _wiring():
    store = InMemoryStore()
    svc = TicketService(store)
    mission = await store.get_mission("FND-142")
    assert mission is not None
    return store, svc, mission


def _stories(tickets):
    return [t for t in tickets if str(t.kind) == TicketKind.STORY]


async def test_plan_stories_creates_todo_stories_with_dependency_links():
    store, svc, mission = await _wiring()
    slices = [
        {"subtask_id": "T1", "title": "API schema", "role": "backend", "skills": ["api-design"],
         "files": ["src/api.py"], "depends_on": []},
        {"subtask_id": "T2", "title": "UI board", "role": "frontend", "skills": ["react"],
         "files": ["ui/App.tsx"], "depends_on": ["T1"]},
    ]
    await svc.plan_stories(mission, slices)

    stories = _stories(await store.list_tickets(mission_id=mission.id))
    assert len(stories) == 2
    # Created UP FRONT, in To Do — before any build.
    assert all(str(s.status) == TicketStatus.TODO for s in stories)
    by_sid = {s.subtask_id: s for s in stories}
    assert by_sid["T1"].agent_role == "backend" and by_sid["T2"].agent_role == "frontend"
    # A dependency edge exists (T1 blocks T2).
    t1_links = await store.list_ticket_links(by_sid["T1"].id)
    assert any(link.from_ticket == by_sid["T1"].id and link.to_ticket == by_sid["T2"].id
               for link in t1_links)

    # Idempotent: re-planning does not duplicate stories.
    await svc.plan_stories(mission, slices)
    assert len(_stories(await store.list_tickets(mission_id=mission.id))) == 2


async def test_on_build_start_moves_todo_to_in_progress_and_assigns_owner():
    store, svc, mission = await _wiring()
    await svc.plan_stories(mission, [
        {"subtask_id": "build", "title": mission.title, "role": "backend",
         "instructions": "build the whole app", "skills": [], "files": [], "depends_on": []},
    ])
    story = await store.find_ticket(mission_id=mission.id, kind=TicketKind.STORY, subtask_id="build")
    assert str(story.status) == TicketStatus.TODO and not story.agent_name

    # Build start assigns the real engineer and moves it In Progress (fix for "In Progress empty").
    await svc.on_build_start(mission, [
        {"subtask_id": "build", "title": mission.title, "role": "backend",
         "agent_name": "Ada", "instructions": "build the whole app"},
    ])
    story = await store.get_ticket(story.id)
    assert str(story.status) == TicketStatus.IN_PROGRESS
    assert story.agent_name == "Ada"  # owner filled in at build start


async def test_on_build_start_creates_missing_ticket_so_board_matches_the_build():
    """Divergence fix: if the plan didn't pre-create a story (e.g. the build re-decomposed on a
    resume), on_build_start creates one from the ACTUAL subtask — so Tickets == Live Build."""
    store, svc, mission = await _wiring()
    await svc.on_build_start(mission, [
        {"subtask_id": "api", "title": "REST API", "role": "backend", "agent_name": "Ada",
         "instructions": "Implement GET/POST/DELETE /tasks"},
        {"subtask_id": "ui", "title": "Frontend UI", "role": "frontend", "agent_name": "Ivy",
         "instructions": "List/add/delete tasks", "depends_on": ["api"]},
    ])
    api = await store.find_ticket(mission_id=mission.id, kind=TicketKind.STORY, subtask_id="api")
    ui = await store.find_ticket(mission_id=mission.id, kind=TicketKind.STORY, subtask_id="ui")
    assert api and ui
    assert str(api.status) == TicketStatus.IN_PROGRESS and api.agent_name == "Ada"
    assert str(ui.status) == TicketStatus.IN_PROGRESS and ui.agent_name == "Ivy"
    assert ui.agent_role == "frontend"
    # The description carries what the ticket actually DOES (its instructions), not just the title.
    assert "GET/POST/DELETE" in (api.description or {}).get("goal", "")


def test_parse_subtasks_keeps_only_backward_acyclic_deps():
    # T2 depends on T1 (backward → kept); T1 "depends on" T2 (forward → dropped, would be a cycle).
    text = (
        '[{"id":"T1","title":"schema","role":"backend","files":["a.py"],'
        '"depends_on":["T2"],"instructions":"define schema","produces":"the API schema"},'
        '{"id":"T2","title":"ui","role":"frontend","files":["b.tsx"],'
        '"depends_on":["T1"],"instructions":"build UI","produces":null}]'
    )
    subs = devloop._parse_subtasks(text)
    assert len(subs) == 2
    t1, t2 = subs[0], subs[1]
    assert t1.depends_on == []             # forward edge to a not-yet-seen T2 was dropped (acyclic guard)
    assert t2.depends_on == ["schema"]     # backward edge kept, resolved from id "T1" → its title
    assert t1.produces == "the API schema"
    assert t2.produces == ""            # "null" normalised to empty
    assert t1.role == "backend" and t2.role == "frontend"


def test_acceptance_criteria_reach_the_ticket_and_the_dev_prompt():
    from app import prompts
    # (1) the planner's acceptance list is parsed onto the Subtask
    text = ('[{"id":"T1","title":"api","role":"backend","files":["a.py"],'
            '"instructions":"build the api","acceptance":["curl localhost:8000/tasks returns 200"]}]')
    st = devloop._parse_subtasks(text)[0]
    assert st.acceptance == ["curl localhost:8000/tasks returns 200"]
    # (2) the dev's build prompt embeds those criteria, so the dev codes to meet them
    task = prompts.subtask_task(_mission_ns(), st)
    assert "ACCEPTANCE CRITERIA" in task
    assert "curl localhost:8000/tasks returns 200" in task


def _mission_ns():
    from types import SimpleNamespace
    return SimpleNamespace(title="Task board", requirements="", summary="")


async def test_planned_story_description_carries_acceptance_criteria():
    store, svc, mission = await _wiring()
    await svc.plan_stories(mission, [{
        "subtask_id": "T1", "title": "REST API", "role": "backend",
        "instructions": "Implement GET/POST/DELETE /tasks",
        "acceptance": ["GET /tasks returns 200 JSON array", "POST creates a task"],
        "skills": ["python"], "files": ["api.py"], "depends_on": [],
    }])
    story = await store.find_ticket(mission_id=mission.id, kind=TicketKind.STORY, subtask_id="T1")
    assert story.description.get("acceptance") == ["GET /tasks returns 200 JSON array", "POST creates a task"]
    assert story.description.get("goal") == "Implement GET/POST/DELETE /tasks"


async def test_qa_and_reviewer_names_are_recorded_on_tickets():
    store, svc, mission = await _wiring()
    await svc.plan_stories(mission, [{"subtask_id": "T1", "title": "API", "role": "backend",
                                      "instructions": "build it", "acceptance": ["works"]}])
    await svc.on_build_start(mission, [{"subtask_id": "T1", "title": "API", "role": "backend",
                                        "agent_name": "Ada", "instructions": "build it"}])

    # QA runs and FAILS → the bug is owned by the named QA engineer (not a generic 'QA').
    await svc.on_qa(mission, "run-1", passed=False, reason="endpoint 500s", actor=("Ansa", "qa"))
    bug = await store.find_ticket(mission_id=mission.id, kind=TicketKind.BUG, run_id="run-1")
    assert bug is not None and bug.agent_name == "Ansa" and bug.agent_role == "qa"
    created = [e for e in await store.list_ticket_events(bug.id) if str(e.kind) == "created"]
    assert created and created[0].actor_name == "Ansa"

    # Rebuild → code REVIEW approves → the story advances to QA, attributed to the reviewer (CTO).
    # (Pipeline order is build → review → QA → ship.)
    await svc.on_build_start(mission, [{"subtask_id": "T1", "title": "API", "role": "backend",
                                        "agent_name": "Ada", "instructions": "build it"}])
    await svc.on_reviewed(mission, approved=True, actor=("Kamran", "cto"), reason="clean")
    story = await store.find_ticket(mission_id=mission.id, kind=TicketKind.STORY, subtask_id="T1")
    assert str(story.status) == TicketStatus.QA
    to_qa = [e for e in await store.list_ticket_events(story.id)
             if str(e.to_status) == TicketStatus.QA]
    assert to_qa and to_qa[-1].actor_name == "Kamran"
    assert "approved" in (to_qa[-1].body or {}).get("reason", "").lower()

    # QA PASSES → a 'QA passed' comment on the story names the QA engineer (final gate before ship).
    await svc.on_qa(mission, "run-2", passed=True, reason="all green", actor=("Ansa", "qa"))
    qa_comments = [e for e in await store.list_ticket_events(story.id)
                   if str(e.kind) == "commented" and "qa passed" in (e.body or {}).get("reason", "").lower()]
    assert qa_comments and qa_comments[-1].actor_name == "Ansa"


async def test_qa_bug_is_worked_during_rework_then_closed_on_qa_pass():
    """A QA-filed bug must not sit stranded in To Do: during rework it's handed to the engineer fixing
    the blocked story (In Progress), and it's resolved (Done) once QA re-passes."""
    store, svc, mission = await _wiring()
    await svc.on_build_start(mission, [{"subtask_id": "api", "title": "API", "role": "backend",
                                        "agent_name": "Ada", "instructions": "build the api"}])
    # QA fails → bug filed in To Do, owned by the QA reporter, blocking the story.
    await svc.on_qa(mission, "run-1", passed=False, reason="broken", actor=("Ansa", "qa"))
    bug = await store.find_ticket(mission_id=mission.id, kind=TicketKind.BUG, run_id="run-1")
    assert bug is not None and str(bug.status) == TicketStatus.TODO and bug.agent_name == "Ansa"

    # Rework build starts → the bug is handed to the story's engineer and moved In Progress.
    await svc.on_build_start(mission, [{"subtask_id": "api", "title": "API", "role": "backend",
                                        "agent_name": "Ada", "instructions": "build the api"}])
    bug = await store.get_ticket(bug.id)
    assert str(bug.status) == TicketStatus.IN_PROGRESS and bug.agent_name == "Ada"

    # QA re-passes → the finding is resolved.
    await svc.on_qa(mission, "run-2", passed=True, reason="green", actor=("Ansa", "qa"))
    bug = await store.get_ticket(bug.id)
    assert str(bug.status) == TicketStatus.DONE


def test_acceptance_criteria_are_kept_readable_not_script_blobs():
    # A planner that dumps a whole test SCRIPT as an acceptance line → dropped; prose outcomes kept.
    text = ('[{"title":"api","role":"backend","files":["a.py"],"instructions":"build",'
            '"acceptance":['
            '"python - <<\'PY\'\\nimport subprocess, urllib.request\\nassert True\\nPY",'
            '"GET /timers returns 200 with a JSON array",'
            '"POST /timers creates a timer and returns its id"]}]')
    st = devloop._parse_subtasks(text)[0]
    assert st.acceptance == ["GET /timers returns 200 with a JSON array",
                             "POST /timers creates a timer and returns its id"]
    assert all("import " not in a and "<<" not in a for a in st.acceptance)


def test_dependency_waves_order_tasks_so_agents_build_on_upstream_code():
    # contract (no deps) → wave 0; server + ui (both depend on contract) → wave 1 in parallel;
    # readme (depends on both) → wave 2. Each wave merges before the next runs → coherent code.
    def pair(title, deps=None):
        return (devloop.Subtask(title=title, files=[f"{title}.x"], instructions="build",
                                depends_on=deps or []), (object(), title.title(), "backend"))
    waves = devloop._dependency_waves([
        pair("contract"), pair("server", ["contract"]), pair("ui", ["contract"]),
        pair("readme", ["server", "ui"]),
    ])
    titles = [sorted(st.title for st, _ in w) for w in waves]
    assert titles == [["contract"], ["server", "ui"], ["readme"]]


def test_assign_prefers_agents_whose_skills_cover_the_task():
    # Two backend agents; the task needs "postgres" — assignment must pick the one who has it.
    from types import SimpleNamespace
    a_generic = SimpleNamespace(skills=["python"])
    a_pg = SimpleNamespace(skills=["python", "postgres"])
    specs = [
        (object(), "Generic", "backend", a_generic),
        (object(), "Pg", "backend", a_pg),
    ]
    st = devloop.Subtask(title="db layer", files=["db.py"], instructions="build it",
                         role="backend", skills=["postgres"])
    assignments = devloop._assign([st], specs)
    assert len(assignments) == 1
    _st, chosen = assignments[0]
    assert chosen[1] == "Pg"  # the postgres-skilled agent, not the generic one


async def test_plan_tasks_falls_back_to_decompose_when_plan_shape_is_missing():
    """If the planner can't produce a {"tasks":[...]} object, plan_tasks must still yield a usable
    decomposition (never leave the build with nothing)."""
    class _FlatDecomposeProvider:
        name = "fake"
        model = "fake"

        async def complete(self, *, system, prompt, purpose="", max_tokens=1024):
            from app.providers.base import LLMResult
            # No {"tasks":...} wrapper — a bare disjoint-file array (the decompose shape).
            return LLMResult(
                '[{"title":"api","role":"backend","files":["a.py"],"instructions":"build api"},'
                '{"title":"ui","role":"frontend","files":["b.tsx"],"instructions":"build ui"}]',
                "fake", 0, 0, 0)

    _store, _svc, mission = await _wiring()
    tasks = await devloop.plan_tasks(mission, _FlatDecomposeProvider(), 4)
    assert len(tasks) == 2 and {t.role for t in tasks} == {"backend", "frontend"}
