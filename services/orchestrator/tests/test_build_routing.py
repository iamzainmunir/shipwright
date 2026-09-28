"""Build routing: a real mission must build its OWN brief (greenfield), never fall into the seeded
demo builder (`devloop.real_build`, which hard-codes a tenant-isolation fixture + a canned task). This
was the M-186 bug — a WhatsApp-started 'print hello' mission had no project_kind, so it dropped into the
demo path and looped on off-mission reworks. Greenfield is now the DEFAULT; the demo is opt-in only."""
from __future__ import annotations

from app.engine import RunEngine, _enum_value
from app.events import EventBus
from app.seed import DEMO_ORG, DEMO_WS
from app.store import InMemoryStore
from foundry_core.enums import MissionStage
from foundry_core.ids import new_ulid
from foundry_core.models import Mission

from tests.support.scripted_provider import ScriptedProvider


def _m(kind, path=None) -> Mission:
    return Mission(id=new_ulid(), key="M-1", org_id=DEMO_ORG, workspace_id=DEMO_WS, title="t",
                   stage=MissionStage.BACKLOG, project_kind=kind, project_path=path, requirements="hi")


def test_greenfield_is_the_default_route():
    # No project_kind (WhatsApp/email/text-started) or an explicit 'app' → greenfield, never demo.
    assert RunEngine._build_route(_m(None)) == "app"
    assert RunEngine._build_route(_m("")) == "app"
    assert RunEngine._build_route(_m("app")) == "app"


def test_change_route_requires_a_repo_path():
    assert RunEngine._build_route(_m("change", "/repo")) == "change"
    assert RunEngine._build_route(_m("change", None)) == "app"  # a change with no repo builds greenfield


def test_demo_route_is_explicit_only():
    # The seeded showcase (FND-142) is the ONLY thing that reaches the demo builder.
    assert RunEngine._build_route(_m("demo")) == "demo"


async def test_start_mission_from_text_marks_it_an_app():
    # WhatsApp 'start <brief>' must produce an app mission that builds its brief — not the demo.
    store = InMemoryStore()
    store.model_connections.clear()
    engine = RunEngine(store, EventBus(), ScriptedProvider())
    mission = await engine.start_mission_from_text("print hello from shipwright")
    try:
        assert _enum_value(mission.project_kind) == "app"
    finally:
        await engine.aclose()  # cancel the background run this kicked off
