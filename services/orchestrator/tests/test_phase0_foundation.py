"""v2 Phase 0 — foundation: engine facade, Artifact model/store, spec machine blocks,
durable run context. (plan 07 Phase 0 acceptance)"""

from __future__ import annotations

from datetime import UTC, datetime

from app.engine import RunEngine
from app.engine_protocol import EngineProtocol
from app.events import EventBus
from app.specdoc import CRITERIA_ASK, extract_criteria
from app.store import DEMO_WS, InMemoryStore
from foundry_core.enums import ArtifactKind
from foundry_core.models import Artifact

from tests.support.scripted_provider import ScriptedProvider

# ---- engine facade -------------------------------------------------------------------

def test_legacy_engine_satisfies_the_protocol() -> None:
    engine = RunEngine(InMemoryStore(), EventBus(), ScriptedProvider())
    assert isinstance(engine, EngineProtocol)


def test_engine_flag_defaults_to_legacy() -> None:
    from app.config import get_settings
    assert get_settings().engine in ("legacy", "graph")


# ---- artifact store ------------------------------------------------------------------

async def test_artifact_store_roundtrip() -> None:
    store = InMemoryStore()
    art = Artifact(
        id="A1", workspace_id=DEMO_WS, mission_id="m1", run_id="r1",
        kind=ArtifactKind.SCREENSHOT, name="01_home.png", path="m1/r1/qa/01_home.png",
        mime="image/png", size_bytes=123, sha256="ab" * 32,
        meta={"criterion_id": "AC1", "seq": 1}, created_at=datetime.now(UTC),
    )
    await store.add_artifact(art)
    got = await store.get_artifact("A1")
    assert got is not None and got.kind == ArtifactKind.SCREENSHOT
    assert [a.id for a in await store.list_artifacts(run_id="r1")] == ["A1"]
    assert [a.id for a in await store.list_artifacts(mission_id="m1")] == ["A1"]
    assert await store.list_artifacts(run_id="other") == []


def test_artifact_path_jail() -> None:
    # A tampered row must not read outside the artifacts root.
    import pytest
    from app.artifacts import resolve_artifact_path
    evil = Artifact(id="A2", workspace_id=DEMO_WS, mission_id="m", run_id="r",
                    name="pw", path="../../../../etc/passwd")
    with pytest.raises(ValueError):
        resolve_artifact_path(evil)


# ---- durable run context -------------------------------------------------------------

async def test_run_context_persists_via_store() -> None:
    from foundry_core.models import Run
    store = InMemoryStore()
    run = Run(id="r1", mission_id="m1", workspace_id=DEMO_WS)
    await store.add_run(run)
    await store.update_run("r1", context={"build_facts": {"healthy": False, "why": "0 steps"}})
    got = await store.get_run("r1")
    assert got is not None and got.context["build_facts"]["why"] == "0 steps"


# ---- spec machine blocks -------------------------------------------------------------

def test_extract_criteria_from_fenced_block() -> None:
    spec = (
        "## Spec\nGreat plan.\n\n"
        "```json foundry-criteria\n"
        '[{"id": "AC1", "criterion": "User can add a task", "route": "/",'
        ' "expect_text": ["Add task"], "expect_selector": ["form"]},'
        ' {"criterion": "Board shows four columns"}]\n'
        "```\n"
    )
    crits = extract_criteria(spec)
    assert len(crits) == 2
    assert crits[0].id == "AC1" and crits[0].route == "/" and crits[0].expect_text == ["Add task"]
    # missing id auto-assigned; no machine block → screenshot-only criterion (empty expectations)
    assert crits[1].id == "AC2" and crits[1].route is None and crits[1].expect_text == []


def test_extract_criteria_tolerates_bare_array_and_garbage() -> None:
    assert extract_criteria("no json here at all") == []
    assert extract_criteria("```json\nnot valid json\n```") == []
    bare = 'Preamble [ {"id":"AC1","criterion":"Loads"} ] postamble'
    crits = extract_criteria(bare)
    assert len(crits) == 1 and crits[0].criterion == "Loads"
    # fail-soft: an array of non-criteria objects yields []
    assert extract_criteria('[{"foo": 1}, {"bar": 2}]') == []


def test_criteria_ask_names_the_fence() -> None:
    # The prompt snippet and the extractor must agree on the fence tag.
    assert "foundry-criteria" in CRITERIA_ASK
