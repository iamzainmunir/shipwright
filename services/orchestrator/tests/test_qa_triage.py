"""Severity-based QA triage: the deterministic reducer (_classify_qa) + the PARTIAL follow-up ticket
lifecycle. The reducer is the load-bearing safety net — the LLM proposes a token, but the reducer
reduces (token + ground-truth evidence) to the final disposition, so a mislabelled 'PARTIAL' can
never ship a broken build.
"""

from __future__ import annotations

from app.engine import _classify_qa
from app.store import InMemoryStore
from app.tickets import TicketService
from foundry_core.enums import TicketKind, TicketStatus


def _crit(cid, status, severity="blocking"):
    return {"id": f"criterion:{cid}", "name": cid, "status": status,
            "criterion_id": cid, "severity": severity}


def _ev(checks):
    crit = [c for c in checks if c.get("criterion_id")]
    return {"checks": checks, "crit_total": len(crit),
            "crit_failed": sum(1 for c in crit if c["status"] == "fail")}


# ---- reducer: the never-ship-broken guarantees --------------------------------------------------

def test_unhealthy_build_never_forwards():
    # Even a PASS token can't forward an unhealthy build (ground-truth gate, step 0).
    assert _classify_qa(_ev([_crit("AC1", "pass")]), "PASS", "", healthy=False) == "REWORK"
    assert _classify_qa({}, "PARTIAL", "AC1 missing", healthy=False) == "REWORK"


def test_smoke_floor_failure_is_major():
    # A load/render/console failure is a blocking infra failure → REWORK regardless of the token.
    ev = {"checks": [{"id": "load", "name": "load", "status": "fail"}], "crit_total": 0, "crit_failed": 0}
    assert _classify_qa(ev, "PASS", "", healthy=True) == "REWORK"
    ev2 = {"checks": [{"id": "x", "name": "no console errors", "status": "fail"}], "crit_total": 0, "crit_failed": 0}
    assert _classify_qa(ev2, "PARTIAL", "AC1", healthy=True) == "REWORK"


def test_major_ratio_of_failing_criteria_forces_rework():
    # Half or more of the machine-checked criteria failing = major, even if the model says PARTIAL.
    ev = _ev([_crit("AC1", "pass", "non_blocking"), _crit("AC2", "fail", "non_blocking")])
    assert _classify_qa(ev, "PARTIAL", "AC2 fails", healthy=True) == "REWORK"


def test_any_blocking_criterion_failure_forces_rework():
    # A minority failing (1/3) passes the ratio gate, but a BLOCKING one failing still reopens.
    ev = _ev([_crit("AC1", "pass"), _crit("AC2", "pass"), _crit("AC3", "fail", "blocking")])
    assert _classify_qa(ev, "PARTIAL", "AC3 fails", healthy=True) == "REWORK"


def test_partial_only_when_minor_nonblocking_gap_with_evidence():
    # Core (blocking) pass, one NON-blocking minor fail (1/3), token PARTIAL, reason names it → PARTIAL.
    ev = _ev([_crit("AC1", "pass"), _crit("AC2", "pass"), _crit("AC3", "fail", "non_blocking")])
    assert _classify_qa(ev, "PARTIAL", "AC3 (nice-to-have) is missing", healthy=True) == "PARTIAL"


def test_partial_without_corroboration_falls_back_to_rework():
    ev = _ev([_crit("AC1", "pass"), _crit("AC2", "pass"), _crit("AC3", "fail", "non_blocking")])
    # empty reason → not corroborated → REWORK
    assert _classify_qa(ev, "PARTIAL", "", healthy=True) == "REWORK"
    # prose-only spec (no machine criteria) can't corroborate → REWORK (never forward the unverifiable)
    assert _classify_qa({"checks": [], "crit_total": 0, "crit_failed": 0},
                        "PARTIAL", "AC1 missing", healthy=True) == "REWORK"


def test_clean_pass_and_unknown_token():
    clean = _ev([_crit("AC1", "pass"), _crit("AC2", "pass")])
    assert _classify_qa(clean, "PASS", "", healthy=True) == "PASS"
    assert _classify_qa(clean, "banana", "", healthy=True) == "REWORK"  # unknown token → fail-safe


# ---- follow-up ticket lifecycle -----------------------------------------------------------------

async def _wiring():
    store = InMemoryStore()
    svc = TicketService(store)
    mission = await store.get_mission("FND-142")
    return store, svc, mission


async def test_partial_forwards_stories_and_files_a_pm_assigned_followup():
    store, svc, mission = await _wiring()
    await svc.on_build_start(mission, [{"subtask_id": "ui", "title": "UI", "role": "frontend",
                                        "agent_name": "Ivy", "instructions": "build ui",
                                        "acceptance": ["AC3 dark mode"]}])
    await svc.on_qa_partial(mission, "run-1", reason="AC3 dark mode missing",
                            failing_criteria=["AC3"], qa_actor=("Ansa", "qa"))
    story = await store.find_ticket(mission_id=mission.id, kind=TicketKind.STORY, subtask_id="ui")
    assert str(story.status) == TicketStatus.IN_REVIEW  # forwarded, NOT reopened

    bug = await store.find_ticket(mission_id=mission.id, kind=TicketKind.BUG, run_id="run-1")
    assert bug is not None and str(bug.status) == TicketStatus.TODO
    assert "follow-up" in (bug.labels or []) and bug.priority == "P3"
    assert bug.agent_name == "Ivy"  # assigned to the owning engineer, not QA
    created = [e for e in await store.list_ticket_events(bug.id) if str(e.kind) == "created"]
    assert created and created[0].actor_name == "PM"  # PM triages/files it (separation of duty)
    # linked RELATES (non-blocking), so the story keeps moving
    links = await store.list_ticket_links(bug.id)
    assert any(str(link.link_type) == "relates" for link in links)


async def test_shipping_never_force_closes_a_followup_bug():
    store, svc, mission = await _wiring()
    await svc.on_build_start(mission, [{"subtask_id": "ui", "title": "UI", "role": "frontend",
                                        "agent_name": "Ivy", "instructions": "build ui"}])
    await svc.on_qa_partial(mission, "run-1", reason="AC3 missing", failing_criteria=["AC3"],
                            qa_actor=("Ansa", "qa"))
    bug = await store.find_ticket(mission_id=mission.id, kind=TicketKind.BUG, run_id="run-1")
    await svc.on_shipped(mission)
    bug = await store.get_ticket(bug.id)
    # The known-issue follow-up SURVIVES the ship as backlog (the load-bearing safety guarantee).
    assert str(bug.status) != TicketStatus.DONE
