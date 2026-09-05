"""Parallel (fork-join) multi-agent build.

Covers: subtask parsing (disjoint-file enforcement), the opt-in gate, the git-worktree plumbing
(clean disjoint merges + conflict abort), and the end-to-end fork-join build (decompose → parallel
isolated agents → merge) plus its safe fallbacks. Providers here are deterministic test doubles.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.devloop import _parse_subtasks, build_parallel
from app.engine import RunEngine
from app.sandbox import LocalSandbox

# ── subtask parsing ─────────────────────────────────────────────────────────────────────────

def test_parse_subtasks_valid_with_roles() -> None:
    text = ('[{"title":"API","role":"backend","files":["api.py"],"instructions":"build the api"},'
            '{"title":"UI","role":"frontend","files":["ui.js"],"instructions":"build the ui"}]')
    subs = _parse_subtasks(text)
    assert [s.title for s in subs] == ["API", "UI"]
    assert subs[0].files == ["api.py"] and subs[1].instructions == "build the ui"
    assert subs[0].role == "backend" and subs[1].role == "frontend"


def test_parse_subtasks_role_defaults_and_aliases() -> None:
    text = ('[{"title":"A","instructions":"x","files":["a"]},'          # no role → backend
            '{"title":"B","role":"UI","instructions":"y","files":["b"]},'   # alias → frontend
            '{"title":"C","role":"weird","instructions":"z","files":["c"]}]')  # unknown → backend
    subs = _parse_subtasks(text)
    assert [s.role for s in subs] == ["backend", "frontend", "backend"]


def test_parse_subtasks_drops_overlapping_files() -> None:
    # The 2nd subtask edits a file the 1st already claimed → dropped (keeps the merge conflict-free).
    text = ('[{"title":"A","files":["shared.py"],"instructions":"a"},'
            '{"title":"B","files":["shared.py","b.py"],"instructions":"b"}]')
    subs = _parse_subtasks(text)
    assert len(subs) == 1 and subs[0].title == "A"


def test_parse_subtasks_handles_garbage_and_prose() -> None:
    assert _parse_subtasks("not json at all") == []
    assert _parse_subtasks('{"not": "a list"}') == []
    assert _parse_subtasks("") == []
    # JSON embedded in prose is still extracted.
    subs = _parse_subtasks('Sure! Here:\n[{"title":"X","files":[],"instructions":"do x"}] done')
    assert len(subs) == 1 and subs[0].title == "X"


def test_parse_subtasks_skips_incomplete_items() -> None:
    text = ('[{"title":"","instructions":"no title"},'
            '{"title":"ok","instructions":""},'
            '{"title":"good","instructions":"do it","files":["g.py"]}]')
    subs = _parse_subtasks(text)
    assert [s.title for s in subs] == ["good"]


# ── build mode gates: greenfield is SOLO by default; parallel is opt-IN ───────────────────────

def _m(**kw):
    base = {"labels": [], "requirements": "", "summary": ""}
    base.update(kw)
    return SimpleNamespace(**base)


def test_is_solo_label_forces_single_agent() -> None:
    assert RunEngine._is_solo(_m(labels=["solo"])) is True
    assert RunEngine._is_solo(_m(labels=["Sequential"])) is True
    assert RunEngine._is_solo(_m(labels=["no-parallel"])) is True
    assert RunEngine._is_solo(_m(requirements="build this with no parallel please")) is True
    assert RunEngine._is_solo(_m(requirements="Build a normal todo app")) is False
    assert RunEngine._is_solo(_m(labels=None, requirements=None, summary=None)) is False


def test_wants_parallel_is_opt_in() -> None:
    # Parallel fan-out is OFF by default (greenfield builds solo → coherent app); a `parallel` label
    # or the phrase in the brief opts back in.
    assert RunEngine._wants_parallel(_m()) is False
    assert RunEngine._wants_parallel(_m(requirements="Build a normal todo app")) is False
    assert RunEngine._wants_parallel(_m(labels=["parallel"])) is True
    assert RunEngine._wants_parallel(_m(labels=["multi-agent"])) is True
    assert RunEngine._wants_parallel(_m(requirements="build this in parallel across the team")) is True
    assert RunEngine._wants_parallel(_m(labels=None, requirements=None, summary=None)) is False


# ── git worktree plumbing (real git) ────────────────────────────────────────────────────────

async def test_worktrees_disjoint_files_merge_cleanly(tmp_path) -> None:
    sb = await LocalSandbox.at_path(str(tmp_path / "proj"))
    await sb.init_empty_repo()

    wt1 = await sb.add_worktree("p/one")
    await wt1.write_file("a.txt", "A")
    await wt1.commit_all("add a")
    wt2 = await sb.add_worktree("p/two")
    await wt2.write_file("b.txt", "B")
    await wt2.commit_all("add b")

    ok1, _ = await sb.merge_branch("p/one")
    ok2, _ = await sb.merge_branch("p/two")
    assert ok1 and ok2
    assert (sb.dir / "a.txt").exists() and (sb.dir / "b.txt").exists()

    await sb.remove_worktree_path(wt1.dir)
    await sb.remove_worktree_path(wt2.dir)


async def test_worktree_conflict_aborts_and_reports(tmp_path) -> None:
    sb = await LocalSandbox.at_path(str(tmp_path / "proj2"))
    await sb.init_empty_repo()

    wt1 = await sb.add_worktree("c/one")
    await wt1.write_file("shared.txt", "from one")
    await wt1.commit_all("one")
    wt2 = await sb.add_worktree("c/two")
    await wt2.write_file("shared.txt", "from two")
    await wt2.commit_all("two")

    ok1, _ = await sb.merge_branch("c/one")
    ok2, out = await sb.merge_branch("c/two")
    assert ok1 is True
    assert ok2 is False and out  # conflict on shared.txt → aborted, not a half-merged tree
    # The abort left a clean tree with the first branch's content.
    assert (sb.dir / "shared.txt").read_text() == "from one"


# ── end-to-end fork-join build ──────────────────────────────────────────────────────────────

class _WriterProvider:
    """Test double: as a PLANNER its ``complete`` returns a canned JSON plan; as a BUILD agent its
    ``complete_tools`` writes ONE file (``fname``) then finishes — so N agents produce disjoint files."""

    name = "writer"
    model = "writer-1"

    def __init__(self, *, plan: str | None = None, fname: str | None = None) -> None:
        self.plan = plan
        self.fname = fname

    async def complete(self, *, system, prompt, purpose="", max_tokens=1024):
        from app.providers.base import LLMResult
        text = self.plan if (purpose == "plan" and self.plan) else "ok"
        return LLMResult(text=text, model=self.model, tokens_in=1, tokens_out=1, cost_cents=0)

    async def complete_tools(self, *, system, messages, tools, max_tokens=2048, tool_choice="auto"):
        from app.providers.base import AgentTurn, ToolCall
        if not self.fname:
            return AgentTurn("done", [], 1, 1, 0)
        if f"wrote {self.fname}" in json.dumps(messages):  # already wrote my file → finish
            return AgentTurn("built", [], 1, 1, 0)
        return AgentTurn(None, [ToolCall("w1", "fs_write", {"path": self.fname, "content": "x\n"})], 1, 1, 0)


def _mission(path: str, reqs: str = "Build an app with an API and a UI."):
    return SimpleNamespace(key="P-1", id="p1id0000abcd", title="Parallel Test",
                           requirements=reqs, summary="", project_path=path)


async def test_build_parallel_role_matches_and_guarantees_readme(tmp_path) -> None:
    mission = _mission(str(tmp_path / "proj"))
    plan = ('[{"title":"API","role":"backend","files":["api.py"],"instructions":"build the api"},'
            '{"title":"UI","role":"frontend","files":["ui.js"],"instructions":"build the ui"}]')
    planner = _WriterProvider(plan=plan)
    # A frontend subtask must go to the FRONTEND agent, backend to the backend agent.
    agents = [(_WriterProvider(fname="api.py"), "Rex", "backend"),
              (_WriterProvider(fname="ui.js"), "Ivy", "frontend")]

    result, sb, _path = await build_parallel(mission, planner, agents)
    assert result.branch == "main"
    assert "api.py" in result.files and "ui.js" in result.files
    assert "2 subtask(s) merged" in result.summary
    # Docs-as-code: a README is guaranteed even in a parallel build (was missing before).
    assert "README.md" in result.files
    assert (sb.dir / "README.md").exists()
    await sb.destroy()


async def test_build_parallel_tags_each_agent_distinctly(tmp_path) -> None:
    # Each parallel worker's console events are tagged "<name> · <subtask>" so they're distinguishable
    # (fixes the UI showing an anonymous "Backend" N times).
    mission = _mission(str(tmp_path / "proj-tag"))
    plan = ('[{"title":"API","role":"backend","files":["api.py"],"instructions":"a"},'
            '{"title":"UI","role":"frontend","files":["ui.js"],"instructions":"u"}]')
    planner = _WriterProvider(plan=plan)
    agents = [(_WriterProvider(fname="api.py"), "Rex", "backend"),
              (_WriterProvider(fname="ui.js"), "Ivy", "frontend")]

    seen: list[tuple[str, str]] = []

    def make_cbs(label: str, role: str = ""):
        seen.append((label, role))
        return (None, None)

    _result, sb, _path = await build_parallel(mission, planner, agents, make_callbacks=make_cbs)
    # Each worker is tagged by name+subtask AND its real role (so the UI shows Frontend vs Backend).
    assert ("Rex · API", "backend") in seen
    assert ("Ivy · UI", "frontend") in seen
    await sb.destroy()


async def test_build_parallel_falls_back_with_one_agent(tmp_path) -> None:
    mission = _mission(str(tmp_path / "proj-solo"))
    planner = _WriterProvider(plan='[{"title":"only","role":"backend","files":["x.py"],"instructions":"x"}]')
    agents = [(_WriterProvider(fname="x.py"), "Solo", "backend")]  # 1 agent → single build

    result, sb, _path = await build_parallel(mission, planner, agents)
    assert "x.py" in result.files
    assert "merged in parallel" not in result.summary  # took the single-agent path
    await sb.destroy()


async def test_build_parallel_falls_back_when_not_splittable(tmp_path) -> None:
    mission = _mission(str(tmp_path / "proj-1task"))
    planner = _WriterProvider(plan='[{"title":"one","role":"backend","files":["only.py"],"instructions":"o"}]')
    agents = [(_WriterProvider(fname="only.py"), "A", "backend"),
              (_WriterProvider(fname="unused.py"), "B", "backend")]

    result, sb, _path = await build_parallel(mission, planner, agents)
    # Planner produced a single subtask → not worth parallelising → single build.
    assert "only.py" in result.files
    assert "merged in parallel" not in result.summary
    await sb.destroy()


async def test_targeted_rework_rebuilds_only_the_slice_and_keeps_the_rest(tmp_path) -> None:
    """The critical fix: a targeted rework (allow_solo_fallback=False) rebuilds ONLY the given part
    off the existing codebase — it does NOT regenerate the whole app, and untouched files survive."""
    from app.devloop import Subtask
    mission = _mission(str(tmp_path / "proj-rework"))
    # 1) Build the full two-part app.
    plan = ('[{"title":"API","role":"backend","files":["api.py"],"instructions":"a"},'
            '{"title":"UI","role":"frontend","files":["ui.js"],"instructions":"u"}]')
    agents = [(_WriterProvider(fname="api.py"), "Rex", "backend"),
              (_WriterProvider(fname="ui.js"), "Ivy", "frontend")]
    r1, sb, _ = await build_parallel(mission, _WriterProvider(plan=plan), agents)
    assert "api.py" in r1.files and "ui.js" in r1.files
    assert (sb.dir / "ui.js").read_text()  # frontend file is on disk

    # 2) Targeted rework: ONLY the backend part, a single agent, NO solo fallback.
    be_only = [Subtask("API", ["api.py"], "fix the api", role="backend")]
    be_spec = [(_WriterProvider(fname="api.py"), "Rex", "backend")]
    r2, sb2, _ = await build_parallel(mission, _WriterProvider(), be_spec,
                                      subtasks=be_only, allow_solo_fallback=False)
    assert "1 subtask(s) merged" in r2.summary       # ran the slice, did NOT fall back to a full rebuild
    assert (sb2.dir / "ui.js").exists()              # the untouched frontend file SURVIVED
    assert "api.py" in r2.files and "ui.js" in r2.files  # report is the whole app (root diff)
    await sb.destroy()


def test_extract_cto_question_and_rework_note_reach_the_builder() -> None:
    """QA's fix instructions and the CTO-escalation contract are actually in the builder's prompt."""
    from types import SimpleNamespace

    from app.devloop import _extract_cto_question, _subtask_task
    assert _extract_cto_question("done my analysis\nCTO-QUESTION: which port do I bind?") == "which port do I bind?"
    assert _extract_cto_question("all good, built it") == ""

    st = SimpleNamespace(role="backend", files=["src/server.js"], instructions="build server",
                         title="HTTP server", acceptance=["GET /api/x works"], produces="", agent_name="Rex")
    mission = SimpleNamespace(title="Tasks App")
    task = _subtask_task(mission, st, rework_note="the /api/stats route returns 404 — wire it in")
    assert "QA REWORK" in task and "/api/stats route returns 404" in task
    assert "CTO-QUESTION:" in task  # the escalation contract is always offered
    guided = _subtask_task(mission, st, cto_guidance="Bind to process.env.PORT, default 3000.")
    assert "CTO answered" in guided and "process.env.PORT" in guided


class _AskThenBuild(_WriterProvider):
    """Builds its file, but the FIRST time it's blocked and asks the CTO; once it sees the CTO's answer
    in the task it proceeds and writes the file."""

    async def complete_tools(self, *, system, messages, tools, max_tokens=2048, tool_choice="auto"):
        from app.providers.base import AgentTurn, ToolCall
        blob = json.dumps(messages)
        if "CTO answered" in blob or f"wrote {self.fname}" in blob:
            if f"wrote {self.fname}" in blob:
                return AgentTurn("built", [], 1, 1, 0)
            return AgentTurn(None, [ToolCall("w", "fs_write", {"path": self.fname, "content": "x\n"})], 1, 1, 0)
        return AgentTurn("CTO-QUESTION: which port should I bind to?", [], 1, 1, 0)  # blocked → ask


async def test_builder_escalates_to_cto_then_builds_with_the_answer(tmp_path) -> None:
    """A blocked worker asks the CTO instead of guessing; it's re-run with the answer and completes."""
    from app.devloop import Subtask
    mission = _mission(str(tmp_path / "proj-esc"))
    consulted: list[tuple] = []

    async def consult(question, title, role):
        consulted.append((question, title, role))
        return "Bind to process.env.PORT with default 3000."

    subtasks = [Subtask("API", ["api.py"], "a", role="backend"),
                Subtask("UI", ["ui.js"], "u", role="frontend")]
    agents = [(_AskThenBuild(fname="api.py"), "Rex", "backend"),
              (_WriterProvider(fname="ui.js"), "Ivy", "frontend")]
    result, sb, _ = await build_parallel(mission, _WriterProvider(), agents,
                                         subtasks=subtasks, cto_consult=consult)
    assert consulted and "port" in consulted[0][0].lower()   # the backend agent asked the CTO
    assert "api.py" in result.files and "ui.js" in result.files  # then it built, guided by the answer
    await sb.destroy()


def test_assign_matches_role_then_falls_back() -> None:
    from app.devloop import Subtask, _assign
    subs = [Subtask("ui", ["u.js"], "x", role="frontend"),
            Subtask("api", ["a.py"], "y", role="backend")]
    specs = [(object(), "BE", "backend"), (object(), "FE", "frontend")]
    pairs = _assign(subs, specs)
    # The frontend subtask got the FE agent; the backend subtask got the BE agent.
    assert pairs[0][0].title == "ui" and pairs[0][1][1] == "FE"
    assert pairs[1][0].title == "api" and pairs[1][1][1] == "BE"


# ── parallel build never ships a silent stub (the M-157 merge-loss bug) ───────────────────────

class _DistinctWriter(_WriterProvider):
    """Writes DIFFERENT content to a given path, so two of them targeting the same file truly
    conflict on merge (identical content would auto-merge)."""

    def __init__(self, *, fname: str, content: str) -> None:
        super().__init__(fname=fname)
        self._content = content

    async def complete_tools(self, *, system, messages, tools, max_tokens=2048, tool_choice="auto"):
        from app.providers.base import AgentTurn, ToolCall
        if f"wrote {self.fname}" in json.dumps(messages):
            return AgentTurn("built", [], 1, 1, 0)
        return AgentTurn(None, [ToolCall("w1", "fs_write", {"path": self.fname, "content": self._content})], 1, 1, 0)


async def test_build_parallel_partial_merge_is_flagged_unhealthy(tmp_path) -> None:
    # Defense-in-depth: if two subtasks are (mis)assigned OVERLAPPING ownership of the same file and
    # write different content, the 2nd branch conflicts and is skipped. The result must NOT look
    # clean — it reports the skip and tests_passed=False so the health gate + QA catch the lost work.
    # (With a validated ownership map this can't arise; the tool-level jail also blocks cross-file
    # poaching — so we pass overlapping owned_paths explicitly to exercise the merge-abort path.)
    from app.devloop import Subtask
    mission = _mission(str(tmp_path / "proj-conflict"))
    subtasks = [Subtask(title="A", files=["shared.py"], instructions="a", role="backend"),
                Subtask(title="B", files=["shared.py"], instructions="b", role="frontend")]
    agents = [(_DistinctWriter(fname="shared.py", content="from rex\n"), "Rex", "backend"),
              (_DistinctWriter(fname="shared.py", content="from ivy\n"), "Ivy", "frontend")]

    result, sb, _path = await build_parallel(mission, _WriterProvider(), agents, subtasks=subtasks)
    assert "skipped due to a merge conflict" in result.summary
    assert result.tests_passed is False  # a lost subtask is never reported as clean
    await sb.destroy()


class _FailingProvider(_WriterProvider):
    """A build agent whose tool loop always raises — simulates every parallel worker crashing."""

    async def complete_tools(self, *, system, messages, tools, max_tokens=2048, tool_choice="auto"):
        raise RuntimeError("worker crashed")


async def test_build_parallel_all_workers_fail_does_not_ship_a_stub(tmp_path) -> None:
    # When every worker crashes there is NO merged work. build_parallel must NOT return a 0-step
    # scaffold stub (the M-157 failure) — it falls back to a real single build, which here also fails,
    # so the phase raises loudly instead of shipping an empty app.
    mission = _mission(str(tmp_path / "proj-allfail"))
    plan = ('[{"title":"API","role":"backend","files":["api.py"],"instructions":"a"},'
            '{"title":"UI","role":"frontend","files":["ui.js"],"instructions":"u"}]')
    planner = _WriterProvider(plan=plan)
    agents = [(_FailingProvider(fname="api.py"), "Rex", "backend"),
              (_FailingProvider(fname="ui.js"), "Ivy", "frontend")]

    raised = False
    try:
        result, sb, _path = await build_parallel(mission, planner, agents)
    except Exception:
        raised = True
    else:
        # If it somehow returned, it must at least NOT be a passing, real-work stub.
        healthy_files = [f for f in result.files if f.rsplit("/", 1)[-1].lower()
                         not in {"readme.md", ".gitignore"}]
        assert not (result.steps > 0 and healthy_files and result.tests_passed)
        await sb.destroy()
    assert raised  # every worker failing surfaces loudly, never a silent green stub
