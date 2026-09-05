"""LangGraph execution engine (v2 Phase 3 — plan 02/03). The strangler's final swap.

``GraphEngine`` replaces ONLY the hand-rolled orchestration loop (:meth:`RunEngine._execute`) with a
real, checkpointed LangGraph ``StateGraph``. Everything else — phase execution, verdict parsing, the
ground-truth gate, blockers/gates, tickets, the QA harness, providers, and every EngineProtocol
method — is inherited unchanged, so all 10 invariants and the full test suite hold on this path too.

The decision graph is expressed as a real graph: one node per phase, and conditional edges that route
by the SAME ``_next_phase`` logic the legacy loop uses (build↔QA rework, review→build/CTO, CTO
redesign/rebuild/proceed). LangGraph checkpoints the routing state after every super-step, and a
recursion limit is the structural equivalent of the legacy pipeline safety-stop.

Selected by ``FOUNDRY_ENGINE=graph``; ``legacy`` (the default) keeps the battle-tested loop. This is
built last precisely because it is the riskiest change and benefits from wiring in the finished
ownership build, QA evidence, and ticket pieces.
"""

from __future__ import annotations

from typing import Any, TypedDict

from foundry_core import tracing
from foundry_core.enums import AgentRoleKey, MissionStage, RunStatus

from .engine import (
    _HALT,
    _PHASE_BY_KEY,
    _PIPELINE_SAFETY_STOP,
    CANON_ORDER,
    ProviderError,
    RunEngine,
    _enum_value,
    _now,
)

_END = "__end__"  # router sentinel → LangGraph END


class GraphState(TypedDict, total=False):
    """Routing state the LangGraph checkpointer persists between super-steps. The heavy lifting
    (events, steps, blockers, tickets) lives in the store — this holds only what drives routing."""

    run_id: str
    mission_id: str
    phase_key: str      # entry phase (start_phase); each node knows its own phase by closure
    task_kind: str
    cycles: dict        # rework/escalation loop counters (mutated by _next_phase)
    visits: dict        # per-phase attempt counter (for the agent's context)
    reached: int        # furthest canonical index → monotonic progress
    route: str          # next phase key, or _END
    terminal: str       # None | ship | blocked | failed | cancelled


class GraphEngine(RunEngine):
    """RunEngine whose forward driver is a LangGraph StateGraph (checkpointed). Drop-in: same
    constructor, same EngineProtocol surface."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        from langgraph.checkpoint.memory import MemorySaver
        # One checkpointer for the process; the graph's routing state is snapshotted per super-step.
        self._checkpointer = MemorySaver()
        # Each _execute is an INDEPENDENT logical run (a gate resume re-enters at start_phase, exactly
        # like the legacy loop) — so every invocation gets a fresh thread_id, never continuing a prior
        # (ended) thread's checkpoint.
        self._exec_seq = 0

    async def _execute(self, run_id: str, mission_id: str, *, start_phase: str = "intake") -> None:
        """Drive the mission through the decision graph via LangGraph, then finish/ship or stop."""
        m0 = await self.store.get_mission(mission_id)
        run_attrs = {
            tracing.ATTR_RUN_ID: run_id,
            tracing.ATTR_MISSION_KEY: m0.key if m0 else None,
            tracing.ATTR_AUTONOMY: _enum_value(m0.autonomy) if m0 else None,
            tracing.ATTR_MISSION_SOURCE: _enum_value(m0.source) if m0 else None,
        }
        with tracing.span("mission.run", run_attrs, root=True) as run_span:
            try:
                task_kind = await self._route_task(run_id, mission_id, m0)
                phase_key = start_phase if start_phase in _PHASE_BY_KEY else "intake"
                graph = self._build_graph(run_span)
                state = {
                    "run_id": run_id, "mission_id": mission_id, "phase_key": phase_key,
                    "task_kind": task_kind, "cycles": {}, "visits": {}, "reached": 0,
                    "route": None, "terminal": None,
                }
                self._exec_seq += 1
                thread_id = f"{run_id}:{self._exec_seq}"
                final = await self._invoke(graph, thread_id, state, run_span)
                terminal = (final or {}).get("terminal")
                if terminal == "ship":
                    await self._complete_ship(run_id, mission_id, run_span)
                else:
                    # blocked / failed / cancelled — the phase already recorded a blocker/halt/fail.
                    tracing.set_attributes(run_span, {tracing.ATTR_RESULT: terminal or "blocked"})
            except ProviderError as exc:
                tracing.record_error(run_span, exc)
                tracing.set_attributes(run_span, {tracing.ATTR_RESULT: "failed"})
                await self._fail(run_id, mission_id, f"provider error: {exc}")
            except Exception as exc:  # pragma: no cover - defensive
                tracing.record_error(run_span, exc)
                tracing.set_attributes(run_span, {tracing.ATTR_RESULT: "failed"})
                await self._fail(run_id, mission_id, f"engine error: {exc}")

    # ---- graph construction -----------------------------------------------------
    def _build_graph(self, run_span: tracing.Span):
        """A node per phase; conditional edges route by ``_next_phase``. Cyclic (build↔QA) — bounded
        by the recursion limit, the graph-native equivalent of the legacy safety-stop."""
        from langgraph.graph import END, START, StateGraph

        nodes = list(_PHASE_BY_KEY.keys())
        graph = StateGraph(GraphState)
        path_map = {**{p: p for p in nodes}, _END: END}
        for pk in nodes:
            graph.add_node(pk, self._make_node(pk, run_span))
            graph.add_conditional_edges(pk, _router, path_map)
        # START → the run's entry phase (start_phase, e.g. resuming at "ship" after a gate).
        graph.add_conditional_edges(START, lambda s: s["phase_key"], {p: p for p in nodes})
        return graph.compile(checkpointer=self._checkpointer)

    def _make_node(self, phase_key: str, run_span: tracing.Span):
        """A phase node: run the phase (with the inherited error ladder), update progress, and stash
        the next route. Returns the SAME routing decisions the legacy loop makes."""
        phase = _PHASE_BY_KEY[phase_key]

        async def node(state: dict) -> dict:
            run_id, mission_id = state["run_id"], state["mission_id"]
            # Cooperative cancellation — never advance, gate, or ship a cancelled run.
            cur = await self.store.get_run(run_id)
            if cur is None or _enum_value(cur.status) == "cancelled":
                return {"route": _END, "terminal": "cancelled"}

            visits = dict(state.get("visits") or {})
            visits[phase_key] = visits.get(phase_key, 0) + 1
            cycles = dict(state.get("cycles") or {})
            try:
                outcome = await self._run_phase(run_id, mission_id, phase, run_span,
                                                attempt=visits[phase_key])
            except Exception as exc:  # role already self-healed → CTO → user (inherited ladder)
                retry = await self._escalate_error(run_id, mission_id, phase, exc, cycles)
                if retry is None:
                    tracing.record_error(run_span, exc)
                    return {"route": _END, "terminal": "failed", "visits": visits, "cycles": cycles}
                return {"route": retry, "visits": visits, "cycles": cycles}

            if outcome is None:
                return {"route": _END, "terminal": "cancelled", "visits": visits, "cycles": cycles}

            reached = state.get("reached", 0)
            if phase_key in CANON_ORDER:
                reached = max(reached, CANON_ORDER.index(phase_key))
                await self.store.update_mission(
                    mission_id, progress=min(99, round(reached / (len(CANON_ORDER) - 1) * 100)))

            base = {"visits": visits, "cycles": cycles, "reached": reached}
            if phase_key == "ship":
                # A build ships ONLY through the ship gate's APPROVE_GATE — any other outcome stops.
                term = "ship" if outcome.token == "APPROVE_GATE" else "blocked"
                return {**base, "route": _END, "terminal": term}

            next_key = await self._next_phase(
                run_id, mission_id, phase_key, outcome, cycles, state["task_kind"])
            if next_key is None or next_key == _HALT:
                return {**base, "route": _END, "terminal": "blocked"}
            return {**base, "route": next_key}

        return node

    async def _invoke(self, graph, thread_id: str, state: dict, run_span: tracing.Span) -> dict:
        """Run the compiled graph. A recursion-limit hit is the legacy safety-stop: halt for a human."""
        config = {"configurable": {"thread_id": thread_id},
                  "recursion_limit": _PIPELINE_SAFETY_STOP + 5}
        try:
            return await graph.ainvoke(state, config=config)
        except Exception as exc:  # noqa: BLE001 — GraphRecursionError et al.
            if type(exc).__name__ == "GraphRecursionError":
                await self._halt_for_user(
                    state["run_id"], state["mission_id"], AgentRoleKey.CTO,
                    "the pipeline made too many transitions without converging — needs review")
                tracing.set_attributes(run_span, {tracing.ATTR_RESULT: "blocked"})
                return {"terminal": "blocked"}
            raise

    # ---- shared preamble / completion (mirrors the legacy loop's non-phase logic) ----
    async def _route_task(self, run_id: str, mission_id: str, m0) -> str:
        """Task router: classify the work up front so a pure git op skips the build (as in legacy)."""
        task_kind = await self._classify_task(m0) if m0 else "code"
        if task_kind == "ops" and m0 is not None:
            await self._emit_note(run_id, mission_id, AgentRoleKey.CTO,
                                  "🧭 Task router: pure git/deploy task → DevOps handles it directly, "
                                  "skipping the build.", kind="triage")
            target = self._extract_branch(self._latest_directive(m0))
            if target and target != m0.branch:
                await self.store.update_mission(mission_id, branch=target)
                await self._emit_note(run_id, mission_id, AgentRoleKey.DEVOPS,
                                      f"🌿 DevOps will push the current code to branch `{target}`.",
                                      kind="branch")
        elif task_kind == "docs":
            await self._emit_note(run_id, mission_id, AgentRoleKey.CTO,
                                  "🧭 Task router: docs change → the build will edit the docs, then "
                                  "QA + review as usual.", kind="triage")
        return task_kind

    async def _complete_ship(self, run_id: str, mission_id: str, run_span: tracing.Span) -> None:
        """Finalize a merge-approved run: succeed the run, ship the mission, remember + emit."""
        final = await self.store.get_run(run_id)
        if final is None or _enum_value(final.status) == "cancelled":
            return  # cancelled during the last phase → must not ship
        await self.store.update_run(run_id, status=RunStatus.SUCCEEDED, finished_at=_now())
        await self.store.update_mission(
            mission_id, stage=MissionStage.SHIPPED, progress=100, is_blocked=False)
        tracing.set_attributes(run_span, {tracing.ATTR_RESULT: "shipped"})
        m = await self.store.get_mission(mission_id)
        r = await self.store.get_run(run_id)
        if m and r:
            await self._bump_agent_stat(AgentRoleKey.DEVOPS, "shipped", m.workspace_id, mission=m)
            await self._reset_agents_idle(m.workspace_id)
            await self._persist_memory(m)
            await self._persist_skill(m)
            await self._emit(r, m, AgentRoleKey.DEVOPS, "deploy",
                             f"✓ {m.key} — pipeline complete, merge approved")


def _router(state: dict) -> str:
    """Conditional-edge selector: the route the node computed, or END."""
    return state.get("route") or _END
