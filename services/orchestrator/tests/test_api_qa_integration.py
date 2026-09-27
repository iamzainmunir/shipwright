"""P1 — the API-QA rung wired into the engine: a failing blocking API contract item becomes a graded
criterion (so it reopens the build) and is recorded in the feedback ledger."""
from __future__ import annotations

from app import qa_harness
from app.engine import RunEngine, _classify_qa
from app.events import EventBus
from app.qa_harness.checks import CheckResult
from app.qa_harness.harness import QaEvidence
from app.store import InMemoryStore
from foundry_core.enums import RunStatus
from foundry_core.ids import new_ulid
from foundry_core.models import Contract, ContractItem, Run

from tests.support.scripted_provider import ScriptedProvider


def test_classify_qa_reopens_on_blocking_api_failure():
    # A blocking API criterion that FAILED must force REWORK even on a confident LLM PASS.
    evidence = {"checks": [
        {"id": "api:API1:status", "name": "status", "status": "fail",
         "criterion_id": "api:API1", "severity": "blocking", "detail": "GET /api/tasks → 500"},
    ], "crit_total": 1, "crit_failed": 1}
    assert _classify_qa(evidence, "PASS", "", healthy=True) == "REWORK"


def test_classify_qa_ignores_advisory_api_warn():
    # A non-fail (advisory) API check must NOT reopen a clean pass.
    evidence = {"checks": [
        {"id": "api:API1:content-type", "name": "content-type", "status": "warn",
         "criterion_id": None, "severity": None, "detail": "text/plain"},
    ], "crit_total": 0, "crit_failed": 0}
    assert _classify_qa(evidence, "PASS", "", healthy=True) == "PASS"


async def test_run_qa_evidence_maps_api_failure_and_records_feedback(tmp_path, monkeypatch):
    store = InMemoryStore()
    store.model_connections.clear()
    engine = RunEngine(store, EventBus(), ScriptedProvider())
    mission = (await store.list_missions())[0]
    # a real on-disk path so _run_qa_evidence proceeds
    await store.update_mission(mission.id, project_path=str(tmp_path))
    mission = await store.get_mission(mission.id)
    run = await store.add_run(Run(id=new_ulid(), mission_id=mission.id,
        workspace_id=mission.workspace_id, status=RunStatus.RUNNING, started_at="2026-01-01T00:00:00"))
    # seed the contract with a blocking API item
    await store.upsert_contract(Contract(
        id="C1", workspace_id=mission.workspace_id, mission_id=mission.id,
        items=[ContractItem(id="API1", kind="api", criterion="list tasks", severity="blocking",
                            method="GET", path="/api/tasks", response={"status": 200})]))

    async def _fake_run(**kwargs):
        assert kwargs.get("api_items"), "contract API items must be passed to the harness"
        return QaEvidence("L0", "real", checks=[
            CheckResult("api:API1:status", "status", "fail", "GET /api/tasks → 500"),
            CheckResult("api:API1:content-type", "content-type", "warn", "text/plain"),
        ])
    monkeypatch.setattr(qa_harness, "run", _fake_run, raising=True)

    await engine._run_qa_evidence(run.id, mission.id, mission)

    ev = engine._build_facts[mission.id]["qa_evidence"]
    status_check = next(c for c in ev["checks"] if c["id"] == "api:API1:status")
    assert status_check["criterion_id"] == "api:API1" and status_check["severity"] == "blocking"
    assert ev["crit_failed"] == 1
    # the failing blocking item reopens the build
    assert _classify_qa(ev, "PASS", "", healthy=True) == "REWORK"
    # and it was recorded in the feedback ledger
    fb = await store.list_contract_feedback("C1")
    assert any(f.item_id == "API1" and f.severity == "blocking" for f in fb)
