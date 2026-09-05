"""End-to-end automation tests for the DECISION-GRAPH pipeline — the core of Shipwright.

These exercise the whole project automation, not a straight line: the build↔QA rework loop, the
review→CTO escalation → redesign/rebuild/proceed, the loop caps that keep every cycle finite, and
reopen-after-ship (a change request starts a new cycle). The deterministic provider used here is a
TEST DOUBLE (``tests/support``), never shipped in the product code.
"""

from __future__ import annotations

import asyncio

from app.engine import (
    _NO_VERDICT,
    MAX_REWORK_CYCLES,
    RunEngine,
    _assess_build,
    _enum_value,
    _parse_verdict,
)
from app.events import EventBus
from app.store import InMemoryStore
from foundry_core.enums import ApprovalDecision, MissionStage, RunStatus

from tests.support.scripted_provider import ScriptedProvider


def _engine(provider: ScriptedProvider) -> tuple[InMemoryStore, RunEngine]:
    """Engine whose runs are driven by the INJECTED provider — the seeded model connections are
    cleared so ``_resolve_providers`` falls back to it (otherwise the seed's provider would win)."""
    store = InMemoryStore()
    store.model_connections.clear()
    return store, RunEngine(store, EventBus(), provider)


async def _drive_to_terminal_or_gate(store: InMemoryStore, run_id: str, *, tries: int = 400) -> str:
    for _ in range(tries):
        r = await store.get_run(run_id)
        if r and r.status in (RunStatus.BLOCKED, RunStatus.SUCCEEDED, RunStatus.FAILED):
            return r.status.value if hasattr(r.status, "value") else str(r.status)
        await asyncio.sleep(0.02)
    raise AssertionError("run did not reach a terminal/gate state in time")


async def _wait_status(store: InMemoryStore, run_id: str, status: RunStatus, *, tries: int = 400) -> None:
    for _ in range(tries):
        r = await store.get_run(run_id)
        if r and r.status == status:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"run did not reach {status} in time")


def _phase_sequence(steps) -> list[str]:
    return [s.phase for s in steps]


# ── verdict parsing (pure) ────────────────────────────────────────────────────────────────────

def test_parse_verdict_reads_last_line_and_reason() -> None:
    assert _parse_verdict("qa", "looks good\nVERDICT: PASS").token == "PASS"
    o = _parse_verdict("qa", "found a bug\nVERDICT: REWORK — delete has no confirm")
    assert o.token == "REWORK" and "confirm" in o.reason
    # review escalate + reason
    o2 = _parse_verdict("review", "VERDICT: ESCALATE — security concern")
    assert o2.token == "ESCALATE" and "security" in o2.reason


def test_parse_verdict_tolerates_markdown() -> None:
    # Real models emit markdown — a "**Verdict:** REWORK" must NOT be read as the PASS default
    # (this exact bug let a broken build sail past QA to the ship gate).
    assert _parse_verdict("qa", "report…\n\n**Verdict:** REWORK — core component missing").token == "REWORK"
    assert _parse_verdict("review", "__Verdict__ : APPROVE").token == "APPROVE"
    assert _parse_verdict("qa", "`VERDICT`: PASS").token == "PASS"
    o = _parse_verdict("qa", "**Verdict:** REWORK — the calculator UI is absent")
    assert o.token == "REWORK" and "calculator" in o.reason


def test_parse_verdict_missing_verdict_is_recoverable_not_pass() -> None:
    # A reply with NO verdict line must NOT be read as PASS (the M-157 fail-forward bug). It returns
    # the _NO_VERDICT sentinel so the engine RE-ELICITS the verdict instead of silently advancing.
    assert _parse_verdict("qa", "the model rambled with no verdict").token == _NO_VERDICT
    assert _parse_verdict("review", "no verdict here").token == _NO_VERDICT
    assert _parse_verdict("cto.decision", "hmm").token == _NO_VERDICT
    # non-decision phases always advance
    assert _parse_verdict("spec", "anything").token == "ADVANCE"


def test_parse_verdict_present_but_unrecognized_fails_safe_not_forward() -> None:
    # A verdict that IS present but isn't the exact token must fail SAFE (hold), never advance.
    # Plain-English negatives a real QA/CTO model emits are recognised as REWORK.
    assert _parse_verdict("qa", "VERDICT: FAIL — no columns, no drag-drop").token == "REWORK"
    assert _parse_verdict("qa", "VERDICT: REJECT").token == "REWORK"
    assert _parse_verdict("qa", "VERDICT: NEEDS WORK — incomplete").token == "REWORK"
    assert _parse_verdict("qa", "VERDICT: DOES NOT MEET the criteria").token == "REWORK"
    assert _parse_verdict("review", "VERDICT: BLOCK — regressions").token == "REWORK"
    # A truly meaningless token also holds (never advances) for a decision phase.
    assert _parse_verdict("qa", "VERDICT: banana").token == "REWORK"


def test_parse_verdict_affirmative_synonyms_map_forward() -> None:
    assert _parse_verdict("qa", "VERDICT: PASS").token == "PASS"
    # PROCEED is affirmative → maps to the QA forward token.
    assert _parse_verdict("qa", "VERDICT: PROCEED").token == "PASS"
    assert _parse_verdict("review", "VERDICT: LGTM").token == "APPROVE"


def test_agent_model_order_puts_selected_binding_first() -> None:
    # "Use the model I selected for this agent": the bound model is tried FIRST, then the failover
    # list — so engineers-on-local vs everyone-on-Groq actually routes per agent at generation time.
    from types import SimpleNamespace as NS
    a = NS(model_binding="qwen2.5-coder:7b",
           models=["qwen2.5:7b", "gemma2:2b", "qwen2.5-coder:7b", "openai/gpt-oss-20b"])
    order = RunEngine._agent_model_order(a)
    assert order[0] == "qwen2.5-coder:7b"          # the SELECTED model wins
    assert order.count("qwen2.5-coder:7b") == 1     # not duplicated
    assert "openai/gpt-oss-20b" in order            # rest kept as failover
    # no binding → just the models list, in order
    assert RunEngine._agent_model_order(NS(model_binding=None, models=["a", "b"])) == ["a", "b"]


def test_assess_build_flags_stub_and_lost_work() -> None:
    # The ground-truth gate a hallucinated verdict can't pass.
    from types import SimpleNamespace as NS
    healthy, _ = _assess_build(NS(files=["src/App.jsx", "README.md"], steps=5, summary="ok"))
    assert healthy is True
    # 0 agent steps → a stub/fallback produced no real work (the M-157 case).
    stub_ok, why = _assess_build(NS(files=["README.md", "src/App.js"], steps=0, summary="ok"))
    assert stub_ok is False and "0 steps" in why
    # Only scaffold files → no real code.
    scaffold_ok, _ = _assess_build(NS(files=["README.md", ".gitignore"], steps=3, summary="ok"))
    assert scaffold_ok is False
    # A parallel merge that lost work to conflicts is not clean.
    lost_ok, _ = _assess_build(NS(files=["a.js"], steps=4, summary="1 merged; 2 skipped due to a merge conflict (x)"))
    assert lost_ok is False


# ── build↔QA rework loop ────────────────────────────────────────────────────────────────────

async def test_qa_rework_loop_then_reaches_gate() -> None:
    store, engine = _engine(ScriptedProvider(qa_fails_first=True))
    mission = (await store.list_missions())[0]
    run = await engine.start_run(mission)
    assert await _drive_to_terminal_or_gate(store, run.id) == "blocked"

    seq = _phase_sequence(await store.list_steps(run.id))
    # Order is build → review → QA (QA is the final gate). A QA fail loops back through build→review→QA,
    # so build.api and qa each appear at least twice, and review precedes the first QA.
    assert seq.count("build.api") >= 2, seq
    assert seq.count("qa") >= 2, seq
    assert seq.index("review") < seq.index("qa"), seq
    # A legible rework note was emitted.
    notes = [e.text for e in await store.list_events(run_id=run.id)
             if (e.payload or {}).get("kind", "").startswith("route.")]
    assert any("back to Backend" in n for n in notes), notes


# ── review → CTO escalation → redesign ──────────────────────────────────────────────────────

async def test_review_escalates_to_cto_then_redesign_reaches_spec_again() -> None:
    store, engine = _engine(ScriptedProvider(
        qa_fails_first=False, review="escalate", cto="redesign_once"))
    mission = (await store.list_missions())[0]
    run = await engine.start_run(mission)
    assert await _drive_to_terminal_or_gate(store, run.id) == "blocked"

    seq = _phase_sequence(await store.list_steps(run.id))
    assert "cto.decision" in seq, seq
    # redesign sends it back to spec, so spec runs a second time.
    assert seq.count("spec") >= 2, seq
    kinds = [(e.payload or {}).get("kind", "") for e in await store.list_events(run_id=run.id)]
    assert "route.escalate" in kinds and "route.redesign" in kinds, kinds


# ── grounding + verdict-integrity (the M-157 defenses) ──────────────────────────────────────

async def test_qa_pass_on_unhealthy_build_is_overridden_to_rework() -> None:
    # Ground-truth gate: even a confident "VERDICT: PASS" cannot ship a build that objectively did no
    # real work (the M-157 stub). The engine overrides it to REWORK.
    from app.engine import _PHASE_BY_KEY
    store, engine = _engine(ScriptedProvider())
    mission = (await store.list_missions())[0]
    run = await engine.start_run(mission)
    assert await _drive_to_terminal_or_gate(store, run.id) == "blocked"  # parked at the ship gate
    engine._build_facts[mission.id] = {"healthy": False, "why": "0 steps — stub/scaffold only"}
    outcome = await engine._decide(run.id, mission.id, _PHASE_BY_KEY["qa"], "Looks great!\nVERDICT: PASS")
    assert outcome.token == "REWORK", outcome
    assert "ground-truth" in outcome.reason


class _TruncatingQAProvider(ScriptedProvider):
    """QA reply whose VERDICT line was truncated off (long analysis, no verdict) — but a focused
    re-elicitation (purpose='verdict') returns the real REWORK. Proves truncation is recoverable."""

    async def complete(self, *, system: str, prompt: str, purpose: str = "", max_tokens: int = 1024):
        from app.providers.base import LLMResult
        if purpose == "qa":
            return LLMResult(text="QA analysis: every acceptance criterion is unmet, the app is a stub…",
                             model="t", tokens_in=1, tokens_out=1, cost_cents=0)
        if purpose == "verdict":
            return LLMResult(text="VERDICT: REWORK — the build is an empty stub",
                             model="t", tokens_in=1, tokens_out=1, cost_cents=0)
        return await super().complete(system=system, prompt=prompt, purpose=purpose, max_tokens=max_tokens)


async def test_truncated_qa_verdict_is_re_elicited_not_read_as_pass() -> None:
    from app.engine import _PHASE_BY_KEY
    store, engine = _engine(_TruncatingQAProvider())
    mission = (await store.list_missions())[0]
    run = await engine.start_run(mission)
    # QA never emits a passable verdict → reworks to the cap → halts for the user (failed, retryable).
    assert await _drive_to_terminal_or_gate(store, run.id) == "failed"
    # The QA text has NO verdict line; _decide must re-elicit it (not default to PASS).
    outcome = await engine._decide(run.id, mission.id, _PHASE_BY_KEY["qa"],
                                   "QA analysis: every acceptance criterion is unmet, the app is a stub…")
    assert outcome.token == "REWORK", outcome


async def test_ensure_build_facts_reconstructs_grounding_on_resume(tmp_path) -> None:
    """Resume robustness: with in-memory facts wiped (a restart), QA grounding is rebuilt from the REAL
    on-disk app (files + diff from root) so a resumed QA judges the actual code — not an empty context
    that would falsely fail features that are actually present (the M-172 /api/stats false-fail)."""
    from app.sandbox import LocalSandbox
    store, engine = _engine(ScriptedProvider())
    proj = tmp_path / "app"
    sb = await LocalSandbox.at_path(str(proj))
    await sb.init_empty_repo()
    await sb.write_file("src/backend/server.js", "// GET /api/stats -> {count}\n")
    await sb.write_file("src/frontend/index.html", "<h1>Notes</h1>\n")
    await sb.commit_all("app")
    mission = (await store.list_missions())[0]
    await store.update_mission(mission.id, project_path=str(proj), project_kind="app")
    mission = await store.get_mission(mission.id)

    engine._build_facts.pop(mission.id, None)  # simulate a restart wiping the in-memory grounding
    await engine._ensure_build_facts("r-fake", mission.id, mission)

    facts = engine._build_facts.get(mission.id)
    assert facts and "src/backend/server.js" in facts["files"]
    assert "src/frontend/index.html" in facts["files"]
    assert facts["healthy"] is True                      # a real on-disk app is healthy grounding
    assert facts["files_written"] == len(facts["files"]) > 0  # not a 0-write stub → guards don't misfire
    await sb.destroy()


def test_failing_subtasks_localizes_rework_to_the_owning_part() -> None:
    """Targeted rework: QA's failing criteria map to the SUBTASK that owns them (by file/title), so the
    next build rebuilds only that part — not the whole app. Unmappable ⇒ empty (caller rebuilds all)."""
    from app.devloop import Subtask
    store, engine = _engine(ScriptedProvider())
    mid = "m-x"
    engine._plans[mid] = [
        Subtask("Backend server", ["src/backend/server.js"], "", role="backend"),
        Subtask("Frontend UI", ["src/frontend/app.js"], "", role="frontend"),
    ]
    ev = {"checks": [{"criterion_id": "AC1", "criterion": "GET /api/notes works",
                      "name": "GET /api/notes", "status": "fail"}]}
    hit = engine._failing_subtasks(mid, ev, "server.js does not implement GET /api/notes")
    assert hit == {"Backend server"}  # only the backend part, not the frontend
    # A failure that names only the UI layer (no file/title match) → role fallback to the frontend part.
    assert engine._failing_subtasks(mid, {}, "the frontend page does not render") == {"Frontend UI"}
    # Nothing to go on → empty, so the caller safely rebuilds everything.
    assert engine._failing_subtasks(mid, {}, "") == set()


async def test_review_rework_targets_only_the_flagged_part() -> None:
    """A code-review change request for the FRONTEND must rebuild only the frontend — not drag the
    backend dev into a UI-only rework (the M-176 'all devs rebuilt for a frontend note' bug)."""
    from app.devloop import Subtask
    from app.engine import Outcome
    store, engine = _engine(ScriptedProvider())
    mission = (await store.list_missions())[0]
    run = await engine.start_run(mission)
    await _drive_to_terminal_or_gate(store, run.id)
    engine._plans[mission.id] = [
        Subtask("Backend server", ["src/server.js"], "", role="backend"),
        Subtask("Frontend UI", ["public/app.js"], "", role="frontend"),
    ]
    cycles = {"review_rework": 0, "cto_decisions": 0}
    nxt = await engine._next_phase(
        run.id, mission.id, "review",
        Outcome("REWORK", "remove inline element.style assignments from buildCard() in public/app.js"),
        cycles)
    assert nxt == "build.api"
    assert engine._rework_targets.get(mission.id) == {"Frontend UI"}  # backend is NOT dragged in


async def test_qa_rework_that_changed_nothing_escalates_instead_of_looping() -> None:
    """No-progress guard: once we've reworked, if the rebuild changed NOTHING (0 net writes) and QA
    still fails, repeating the identical build is pointless — escalate to the CTO rather than burning
    the rest of the rework budget on the same tasks (the M-172 repeat-in-loop symptom)."""
    from app.engine import Outcome
    store, engine = _engine(ScriptedProvider())
    mission = (await store.list_missions())[0]
    run = await engine.start_run(mission)
    await _drive_to_terminal_or_gate(store, run.id)

    # A rework already happened; the latest build did work (steps) but wrote 0 new files.
    engine._build_facts[mission.id] = {"healthy": True, "files_written": 0, "steps": 6}
    cycles = {"qa_rework": 1, "cto_decisions": 0}
    nxt = await engine._next_phase(run.id, mission.id, "qa", Outcome("REWORK", "still failing"), cycles)
    assert nxt == "cto.decision"  # skipped a wasted identical rework
    assert cycles["qa_rework"] == 1  # budget NOT consumed by the stuck cycle

    # Same stuck signal but the CTO budget is spent → halt for the human, never loop forever.
    cycles = {"qa_rework": 2, "cto_decisions": 2}
    nxt = await engine._next_phase(run.id, mission.id, "qa", Outcome("REWORK", "still failing"), cycles)
    assert nxt == "__halt__"  # _halt_for_user parks the run for the user (never ships, never loops)


async def test_qa_rework_with_real_progress_still_reworks_normally() -> None:
    """The guard must NOT fire when the rebuild actually changed files — that's healthy iteration."""
    from app.engine import Outcome
    store, engine = _engine(ScriptedProvider())
    mission = (await store.list_missions())[0]
    run = await engine.start_run(mission)
    await _drive_to_terminal_or_gate(store, run.id)

    engine._build_facts[mission.id] = {"healthy": True, "files_written": 3, "steps": 6}
    cycles = {"qa_rework": 1, "cto_decisions": 0}
    nxt = await engine._next_phase(run.id, mission.id, "qa", Outcome("REWORK", "one gap left"), cycles)
    assert nxt == "build.api"  # normal rework
    assert cycles["qa_rework"] == 2  # budget consumed


# ── loop caps keep cycles finite ────────────────────────────────────────────────────────────

async def test_qa_never_passes_halts_for_user_never_ships() -> None:
    # QA always fails → the pipeline must be FINITE (loop caps) AND FAIL-SAFE: it must NEVER
    # auto-ship an unverified build. After the rework budget + a CTO consultation, it blocks for the
    # user. (Old behaviour force-forwarded a failing build to review→ship — the bug this fixes.)
    store, engine = _engine(ScriptedProvider(qa_fails_first=False, qa_always_fail=True, cto="proceed"))
    mission = (await store.list_missions())[0]
    run = await engine.start_run(mission)
    # It ends as a needs-human FAILURE (retryable), NOT a merge-approval block — so it never pops the
    # push/merge gate over an unshippable build.
    assert await _drive_to_terminal_or_gate(store, run.id) == "failed"

    seq = _phase_sequence(await store.list_steps(run.id))
    assert seq.count("qa") >= MAX_REWORK_CYCLES + 1, seq  # reworked to the cap
    assert "ship" not in seq, seq                          # never reached the merge gate
    m = await store.get_mission(mission.key)
    assert m.stage is MissionStage.STOPPED                 # parked (not shipped, not "building")
    # A clear "needs your attention" signal was raised — and NO approval blocker (which would wrongly
    # render the push/merge gate).
    kinds = [(e.payload or {}).get("kind", "") for e in await store.list_events(run_id=run.id)]
    assert "needs.user" in kinds, kinds
    blockers = await store.list_blockers()
    assert not any(_enum_value(b.kind) == "approval" and b.mission_id == m.id for b in blockers)


# ── reopen-after-ship ───────────────────────────────────────────────────────────────────────

class _BlockingProvider:
    """Parks a run mid-phase: ``complete`` blocks until released, so a test can supersede a run
    that is genuinely executing (not parked at a gate)."""

    name = "blocking"
    model = "blocking-1"

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, *, system, prompt, purpose="", max_tokens=1024):
        from app.providers.base import LLMResult
        self.entered.set()
        await self.release.wait()
        return LLMResult(text="ok", model=self.model, tokens_in=1, tokens_out=1, cost_cents=0)

    async def complete_tools(self, *, system, messages, tools, max_tokens=2048, tool_choice="auto"):
        from app.providers.base import AgentTurn
        self.entered.set()
        await self.release.wait()
        return AgentTurn("done", [], 1, 1, 0)


async def test_supersede_cancels_a_mid_phase_run() -> None:
    """Regression (critical): starting a re-run must HARD-CANCEL a prior run that is mid-phase (has
    no gate future), so it can't become a zombie that ships stale work."""
    store = InMemoryStore()
    store.model_connections.clear()
    bp = _BlockingProvider()
    engine = RunEngine(store, EventBus(), bp)
    mission = (await store.list_missions())[0]

    run1 = await engine.start_run(mission)
    await asyncio.wait_for(bp.entered.wait(), timeout=2)  # run1 is now blocked inside a phase
    task1 = engine._run_tasks.get(run1.id)
    assert task1 is not None and not task1.done()

    run2 = await engine.start_run(mission)  # supersede → must cancel run1
    assert run2.id != run1.id
    assert _enum_value((await store.get_run(run1.id)).status) == "cancelled"
    await asyncio.sleep(0.05)  # let the cancellation land
    assert task1.done()  # the mid-phase coroutine was actually stopped
    assert (await store.get_mission(mission.key)).stage is not MissionStage.SHIPPED

    # cleanup: release the blocked provider and cancel run2's task
    bp.release.set()
    for task in list(engine._tasks):
        task.cancel()


class _ErrorProvider:
    """Every call fails — exercises the error-recovery ladder (role → CTO → user)."""

    name = "erroring"
    model = "err-1"

    async def complete(self, *, system, prompt, purpose="", max_tokens=1024):
        from app.providers.base import ProviderError
        raise ProviderError("simulated model outage")

    async def complete_tools(self, *, system, messages, tools, max_tokens=2048, tool_choice="auto"):
        from app.providers.base import ProviderError
        raise ProviderError("simulated model outage")


async def test_unresolvable_error_escalates_to_user() -> None:
    """When a role can't self-heal and the CTO can't resolve it, the run is handed to the human:
    it fails with an actionable 'needs your input' event (Retry then resumes)."""
    store = InMemoryStore()
    store.model_connections.clear()
    engine = RunEngine(store, EventBus(), _ErrorProvider())
    mission = (await store.list_missions())[0]

    run = await engine.start_run(mission)
    assert await _drive_to_terminal_or_gate(store, run.id) == "failed"
    events = await store.list_events(run_id=run.id)
    kinds = [(e.payload or {}).get("kind") for e in events]
    assert "phase.error" in kinds, kinds        # the role reported its own error
    assert "route.escalate" in kinds, kinds     # …escalated to the CTO
    assert "needs.user" in kinds, kinds         # …and finally involved the human


async def test_cancel_run_stops_and_preserves_context() -> None:
    """Force-stop cancels the live run (task actually stops) while the mission's context stays put
    so it can be retried."""
    store = InMemoryStore()
    store.model_connections.clear()
    bp = _BlockingProvider()
    engine = RunEngine(store, EventBus(), bp)
    mission = (await store.list_missions())[0]
    before_req = mission.requirements

    run = await engine.start_run(mission)
    await asyncio.wait_for(bp.entered.wait(), timeout=2)
    task = engine._run_tasks.get(run.id)

    stopped = await engine.cancel_run(mission)
    assert stopped == 1
    assert _enum_value((await store.get_run(run.id)).status) == "cancelled"
    await asyncio.sleep(0.05)
    assert task is not None and task.done()
    # context preserved (requirements unchanged, mission not shipped)
    m = await store.get_mission(mission.key)
    assert m.requirements == before_req
    assert m.stage is not MissionStage.SHIPPED
    # ...and the mission is parked at STOPPED (not left mid-flight at "building"), so the board and
    # dashboard stop counting it as actively in progress. Retry moves it forward again.
    assert m.stage is MissionStage.STOPPED
    assert m.is_blocked is False
    bp.release.set()


async def test_retry_resumes_from_checkpoint() -> None:
    """After a run reaches the gate, retry resumes from the checkpoint (ship) instead of redoing the
    finished phases — preserving progress."""
    store, engine = _engine(ScriptedProvider(qa_fails_first=False))
    mission = (await store.list_missions())[0]

    run1 = await engine.start_run(mission)
    assert await _drive_to_terminal_or_gate(store, run1.id) == "blocked"  # all phases done, ship gated

    run2 = await engine.retry_run(mission)
    assert run2.id != run1.id
    assert await _drive_to_terminal_or_gate(store, run2.id) == "blocked"
    # The retry did NOT redo intake/spec — its first step is the resumed phase (ship).
    seq2 = _phase_sequence(await store.list_steps(run2.id))
    assert seq2 and seq2[0] == "ship", seq2
    assert "intake" not in seq2, seq2


async def test_resume_phase_uses_latest_run_not_an_older_shipped_one() -> None:
    """A retry after a reopen must resume from the LATEST run's progress (which still needs the
    build), never from an older run that already reached review/ship — else the new change skips the
    build (exactly what let a 'modify the readme' request ship without editing anything)."""
    from foundry_core.enums import AgentRoleKey, RunStatus, StepStatus
    from foundry_core.ids import new_ulid
    from foundry_core.models import Run, Step

    store, engine = _engine(ScriptedProvider(qa_fails_first=False))
    mission = (await store.list_missions())[0]

    async def _run(started: str, done: list[str], gated: str | None = None) -> None:
        r = await store.add_run(Run(id=new_ulid(), mission_id=mission.id,
            workspace_id=mission.workspace_id, status=RunStatus.BLOCKED,
            autonomy=mission.autonomy, started_at=started))
        for p in done:
            await store.add_step(Step(id=new_ulid(), run_id=r.id, phase=p, title=p,
                agent_role=AgentRoleKey.PM, status=StepStatus.DONE, started_at=started))
        if gated:
            await store.add_step(Step(id=new_ulid(), run_id=r.id, phase=gated, title=gated,
                agent_role=AgentRoleKey.DEVOPS, status=StepStatus.GATED, started_at=started))

    # Older run reached review (ship gated); newer run only completed spec (build interrupted).
    # After spec the next checkpoint is the `plan` phase (PM decomposes into tickets before the build).
    await _run("2026-01-01T00:00:00", ["intake", "clarify", "spec", "plan", "build.api", "qa", "review"], gated="ship")
    await _run("2026-01-02T00:00:00", ["intake", "clarify", "spec"])
    assert await engine._resume_phase(mission) == "plan"


async def test_memory_persisted_on_ship() -> None:
    """The org autonomously remembers what it shipped (a PROJECT memory keyed to the mission)."""
    store, engine = _engine(ScriptedProvider(qa_fails_first=False))
    mission = (await store.list_missions())[0]
    run = await engine.start_run(mission)
    assert await _drive_to_terminal_or_gate(store, run.id) == "blocked"
    blk = [b for b in await store.list_blockers() if b.mission_id == mission.id and not b.resolved_at]
    await engine.resolve_blocker(blk[0].id, ApprovalDecision.APPROVE, actor="owner", note=None)
    await _wait_status(store, run.id, RunStatus.SUCCEEDED)

    mems = await store.list_memories(mission.workspace_id)
    assert any(mission.key in m.title for m in mems), [m.title for m in mems]


async def test_change_request_reopens_shipped_mission() -> None:
    store, engine = _engine(ScriptedProvider(qa_fails_first=False))
    mission = (await store.list_missions())[0]

    run1 = await engine.start_run(mission)
    assert await _drive_to_terminal_or_gate(store, run1.id) == "blocked"
    blk = [b for b in await store.list_blockers() if b.mission_id == mission.id and not b.resolved_at]
    await engine.resolve_blocker(blk[0].id, ApprovalDecision.APPROVE, actor="owner", note=None)
    await _wait_status(store, run1.id, RunStatus.SUCCEEDED)  # ship completes asynchronously
    shipped = await store.get_mission(mission.key)
    assert shipped.stage is MissionStage.SHIPPED

    # Owner asks for a change AFTER it shipped → a fresh run starts and reopens the mission.
    run2 = await engine.request_change(shipped, "Add a due-date field to each task")
    assert run2.id != run1.id
    reopened = await store.get_mission(mission.key)
    assert reopened.stage is not MissionStage.SHIPPED  # back in the pipeline
    assert "due-date" in (reopened.requirements or "")
    assert await _drive_to_terminal_or_gate(store, run2.id) == "blocked"  # runs the full cycle again


# ── task router (decider): route work to the right role, skip the build when it's not needed ────

def test_extract_branch_pulls_target_from_free_text() -> None:
    ex = RunEngine._extract_branch
    assert ex("Push the same code to a new branch named feature/todo-app") == "feature/todo-app"
    assert ex("push it into release/2.0") == "release/2.0"
    assert ex("create a branch hotfix-42 and push") == "hotfix-42"
    assert ex("Push the existing code to the main branch. No code changes") == "main"
    assert ex("Add a due-date field to each task") is None  # no branch named → keep existing


async def test_ops_task_skips_build_and_targets_named_branch() -> None:
    store, engine = _engine(ScriptedProvider(qa_fails_first=False))
    mission = (await store.list_missions())[0]
    # An ops request: no code change, just push the existing project to a named branch.
    mission = await store.update_mission(
        mission.id,
        requirements="Push the existing project to a new branch named feature/login. No changes.",
    )
    run = await engine.start_run(mission)
    assert await _drive_to_terminal_or_gate(store, run.id) in ("blocked", "succeeded")

    seq = _phase_sequence(await store.list_steps(run.id))
    # The heavy phases are skipped for an ops task; it goes spec → review → ship.
    assert "build.api" not in seq, seq
    assert "qa" not in seq, seq
    assert "review" in seq and "ship" in seq, seq
    # A router note explains the decision, and DevOps targets the requested branch.
    notes = [e.text for e in await store.list_events(run_id=run.id)]
    assert any("Task router" in n and "git" in n.lower() for n in notes), notes
    assert (await store.get_mission(mission.id)).branch == "feature/login"


async def test_code_task_still_runs_full_build() -> None:
    store, engine = _engine(ScriptedProvider(qa_fails_first=False))
    mission = (await store.list_missions())[0]
    mission = await store.update_mission(
        mission.id, requirements="Add a settings screen with a dark-mode toggle.")
    run = await engine.start_run(mission)
    assert await _drive_to_terminal_or_gate(store, run.id) in ("blocked", "succeeded")
    seq = _phase_sequence(await store.list_steps(run.id))
    assert "build.api" in seq and "qa" in seq, seq  # a code task keeps the build + QA


def test_latest_directive_is_the_last_change_request() -> None:
    from types import SimpleNamespace
    m = SimpleNamespace(
        requirements="Build a TODO app.\n\nChange request:\nfirst\n\nChange request:\nmodify the readme",
        summary=None, title=None)
    assert RunEngine._latest_directive(m) == "modify the readme"
    m2 = SimpleNamespace(requirements="Build a TODO app.", summary=None, title=None)
    assert RunEngine._latest_directive(m2) == "Build a TODO app."


async def test_router_classifies_readme_edit_as_docs_not_ops_despite_stale_history() -> None:
    # The exact bug the user hit: old "just push, no changes" requests must NOT shadow a NEW
    # "modify the readme" request. The latest instruction wins → docs (which runs the build).
    store, engine = _engine(ScriptedProvider(qa_fails_first=False))
    mission = (await store.list_missions())[0]
    stale = ("Build a TODO app.\n\n"
             "Change request:\nPush the existing code to main. No code changes — just push.\n\n"
             "Change request:\nPlease modify the readme file with full details and push to main.")
    mission = await store.update_mission(mission.id, requirements=stale)
    assert await engine._classify_task(mission) == "docs"


async def test_docs_task_runs_the_build_to_edit_the_file() -> None:
    store, engine = _engine(ScriptedProvider(qa_fails_first=False))
    mission = (await store.list_missions())[0]
    mission = await store.update_mission(
        mission.id, requirements="Update the README with setup and usage instructions.")
    run = await engine.start_run(mission)
    assert await _drive_to_terminal_or_gate(store, run.id) in ("blocked", "succeeded")
    seq = _phase_sequence(await store.list_steps(run.id))
    assert "build.api" in seq, seq  # a docs edit must go through the build (someone edits the file)


# ── merge gate re-opens on a rejected push (never ships or switches branches on its own) ────────

async def test_rejected_push_reopens_gate_then_ships_on_second_approval() -> None:
    store, engine = _engine(ScriptedProvider(qa_fails_first=False))
    mission = (await store.list_missions())[0]

    # First push "needs the user" (e.g. rejected), the second succeeds — mirrors the user authorizing
    # a force-push or picking a different branch at the re-opened gate.
    calls = {"n": 0}

    async def flaky_push(run_id: str, mission_id: str) -> bool:
        calls["n"] += 1
        return calls["n"] != 1  # False the first time (re-gate), True after

    engine._open_pr_if_built = flaky_push  # type: ignore[assignment]

    run = await engine.start_run(mission)
    assert await _drive_to_terminal_or_gate(store, run.id) == "blocked"

    async def _open_approval():
        return [b for b in await store.list_blockers()
                if b.mission_id == mission.id and b.kind == "approval" and not b.resolved_at]

    async def _wait_new_gate(prev_id: str, *, tries: int = 400):
        for _ in range(tries):
            opens = await _open_approval()
            if opens and opens[0].id != prev_id:
                return opens[0]
            await asyncio.sleep(0.02)
        raise AssertionError("gate did not re-open after the rejected push")

    blk = await _open_approval()
    assert len(blk) == 1
    await engine.resolve_blocker(blk[0].id, ApprovalDecision.APPROVE, actor="owner", note=None)

    # The push was rejected → the gate must RE-OPEN with a FRESH blocker, NOT ship.
    blk2 = await _wait_new_gate(blk[0].id)
    assert (await store.get_mission(mission.id)).stage is not MissionStage.SHIPPED
    await engine.resolve_blocker(blk2.id, ApprovalDecision.APPROVE, actor="owner", note=None)

    await _wait_status(store, run.id, RunStatus.SUCCEEDED)
    assert (await store.get_mission(mission.id)).stage is MissionStage.SHIPPED
    assert calls["n"] == 2
