"""The real dev loop (doc 08) — turn a mission into a branch + commit + diff + PR.

Phase 4: the change is authored by the **agent** driving fs/cmd/git tools (``app/agent.py``)
against the sandbox — not a hardcoded edit. The demo repo seeds a cross-tenant bug + a test
that encodes the guard; the agent reads, fixes, and runs the test until it's green. With a real
Anthropic key the model does this itself; offline, the deterministic provider drives the same
tool loop. In Phase 3b the seed is replaced by cloning the mission's real target repo.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from foundry_core.models import Mission

from . import prompts
from .agent import OnEvent, OnTurn, run_agent
from .connectors import GitHubConnector, PullRequestResult
from .providers.base import LLMProvider
from .sandbox import LocalSandbox

MAX_PARALLEL_SUBTASKS = 6  # cap on how many agents fan out for one parallel build (matches team size)

# Build/decompose prompts now live in the central prompt library (app/prompts.py). Kept as aliases
# so existing imports (`from .devloop import BUILD_SYSTEM`, tests, etc.) keep working unchanged.
BUILD_SYSTEM = prompts.BUILD_SYSTEM

VULN_HANDLER = '''"""Record read — VULNERABLE: ignores the caller's tenant (cross-tenant leak)."""

_DB = {
    "r1": {"id": "r1", "workspace": "A", "amount": 100},
    "r2": {"id": "r2", "workspace": "B", "amount": 200},
}


def get_record(record_id, ctx):
    return _DB.get(record_id)
'''

TEST_SCRIPT = '''import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from handler import get_record


def main():
    assert get_record("r1", {"workspace": "A"}) is not None, "same-tenant read must work"
    assert get_record("r2", {"workspace": "A"}) is None, "cross-tenant read must be blocked"
    print("2 passed")


main()
'''

TASK = (
    "There is a cross-tenant data leak in src/handler.py: get_record(record_id, ctx) returns any "
    "record regardless of the caller's tenant. Fix it so it returns None unless the row's "
    "'workspace' equals ctx['workspace']. Then run `python3 tests/run_tests.py` and iterate until "
    "the tests pass. Read the file before editing; write the entire file."
)


@dataclass(slots=True)
class BuildResult:
    branch: str
    diff: str
    files: list[str]
    tests_passed: bool
    summary: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cents: int = 0
    steps: int = 0
    files_written: int = 0  # successful fs_write calls — the direct "did the build produce work" signal


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "change"


async def real_build(
    mission: Mission, provider: LLMProvider, *, sandbox_root: str | None = None,
    on_event: OnEvent | None = None, on_turn: OnTurn | None = None,
) -> tuple[BuildResult, LocalSandbox]:
    """Provision a sandbox and let the agent make + test the change; capture branch/diff."""
    sb = await LocalSandbox.create(sandbox_root, f"{mission.key}-{mission.id[:8]}")
    await sb.init_repo({
        ".gitignore": "__pycache__/\n*.pyc\n",
        "src/handler.py": VULN_HANDLER,
        "tests/run_tests.py": TEST_SCRIPT,
    })
    branch = f"fix/{mission.key}-{_slug(mission.title)}"
    await sb.checkout_branch(branch)

    result = await run_agent(provider, sb, TASK, on_event=on_event, on_turn=on_turn)

    await sb.commit_all(f"{mission.key}: scope record reads to the caller's tenant")
    diff = await sb.diff("main")
    files = await sb.changed_files("main")
    summary = "; ".join(result.tool_log[-4:]) or result.final_text
    return (
        BuildResult(
            branch=branch, diff=diff, files=files, tests_passed=result.tests_passed,
            summary=summary, tokens_in=result.tokens_in, tokens_out=result.tokens_out,
            cost_cents=result.cost_cents, steps=result.steps, files_written=result.files_written,
        ),
        sb,
    )


def default_project_path(projects_root: str, mission: Mission) -> str:
    """Where a greenfield app is built when the mission doesn't specify a path."""
    root = Path(projects_root).expanduser() if projects_root else Path.home() / "ShipwrightProjects"
    return str(root / f"{mission.key.lower()}-{_slug(mission.title)}")


def _build_task(mission: Mission) -> str:
    """Greenfield/reopened build task — delegates to the central prompt library."""
    return prompts.build_task(mission)


async def build_from_mission(
    mission: Mission, provider: LLMProvider, *, projects_root: str = "",
    on_event: OnEvent | None = None, on_turn: OnTurn | None = None, max_steps: int = 16,
) -> tuple[BuildResult, LocalSandbox, str]:
    """Greenfield build: create the app in a real, persistent project directory driven by the
    mission's own requirements (not a hardcoded task). The directory IS the deliverable."""
    path = mission.project_path or default_project_path(projects_root, mission)
    sb = await LocalSandbox.at_path(path)
    if not await sb.has_repo():
        if not await sb.is_empty():
            raise RuntimeError(f"target directory is not empty: {path}")
        await sb.init_empty_repo()

    result = await run_agent(
        provider, sb, _build_task(mission), max_steps=max_steps, system=BUILD_SYSTEM,
        on_event=on_event, on_turn=on_turn,
    )

    await sb.commit_all(f"{mission.key}: {mission.title}")
    # Report the WHOLE app (diff/files from the repo root), not just this run's delta — so a rework
    # cycle's QA/health grounding sees the complete deliverable, not a shrinking incremental diff.
    base = await sb.root_sha()
    diff = await sb.diff(base)
    files = await sb.changed_files(base)
    summary = result.final_text.strip() or "; ".join(result.tool_log[-4:])
    return (
        BuildResult(
            branch="main", diff=diff, files=files, tests_passed=result.tests_passed,
            summary=summary, tokens_in=result.tokens_in, tokens_out=result.tokens_out,
            cost_cents=result.cost_cents, steps=result.steps, files_written=result.files_written,
        ),
        sb,
        path,
    )


# Decompose/plan prompts live in the central prompt library now (kept as aliases for back-compat).
DECOMPOSE_SYSTEM = prompts.DECOMPOSE_SYSTEM

_ROLE_ALIASES = {"frontend": "frontend", "front-end": "frontend", "fe": "frontend", "ui": "frontend",
                 "backend": "backend", "back-end": "backend", "be": "backend", "api": "backend",
                 "fullstack": "fullstack", "full-stack": "fullstack"}


def _norm_role(value: str) -> str:
    return _ROLE_ALIASES.get(str(value or "").strip().lower(), "backend")


@dataclass(slots=True)
class Subtask:
    title: str
    files: list[str]
    instructions: str
    role: str = "backend"  # frontend | backend | fullstack — which role should build this part
    depends_on: list[str] = field(default_factory=list)  # task ids (titles) this waits on — a DAG edge
    skills: list[str] = field(default_factory=list)       # skills the task needs (for role+skill assign)
    produces: str = ""                                    # the contract this task publishes downstream
    acceptance: list[str] = field(default_factory=list)   # machine-checkable acceptance criteria


# Markers that betray a shell/Python test SCRIPT leaking into an acceptance line (which should be a
# short human-readable outcome, not code). Such items are dropped so the ticket stays readable.
_SCRIPT_MARKERS = ("<<'", '<<"', "import ", "subprocess", "urllib", "def ", "assert ", "os.system",
                   "```", "#!/", "&&", "curl -", "python -")


def _clean_criteria(items: object) -> list[str]:
    """Normalise acceptance criteria into short, human-readable one-liners. Collapses whitespace,
    drops anything that is actually a script/heredoc (models sometimes emit a whole test), and caps
    length — so the ticket shows crisp criteria, never a broken wall of code."""
    out: list[str] = []
    for raw in (items if isinstance(items, list) else []):
        a = " ".join(str(raw).split())  # collapse newlines/tabs/runs of spaces
        if not a:
            continue
        low = a.lower()
        if any(m in low for m in _SCRIPT_MARKERS):
            continue  # a code/script leaked in — skip it (prompt asks for prose outcomes)
        if len(a) > 200:
            a = a[:197].rstrip() + "…"
        out.append(a)
        if len(out) >= 6:
            break
    return out


def _parse_subtasks(text: str) -> list[Subtask]:
    """Parse the planner's JSON array of subtasks. Drops malformed items and any subtask whose files
    OVERLAP an earlier one (disjointness is what makes the parallel merge safe). Returns [] on failure."""
    raw = (text or "").strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        items = json.loads(raw[start : end + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(items, list):
        return []
    out: list[Subtask] = []
    claimed: set[str] = set()
    seen_titles: set[str] = set()   # titles seen SO FAR — a depends_on may only point backward
    id_to_title: dict[str, str] = {}  # planner id (e.g. "T1") → title, to resolve depends_on to titles
    for it in items:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        instructions = str(it.get("instructions") or "").strip()
        files = [str(f).strip() for f in (it.get("files") or []) if str(f).strip()]
        if not title or not instructions:
            continue
        if files and any(f in claimed for f in files):
            continue  # overlaps an earlier subtask → drop it (keep the merge conflict-free)
        # Resolve depends_on to TITLES (the planner may use ids like "T1" OR titles) and keep only
        # edges pointing at an EARLIER task — so ids/titles are consistent everywhere (ticket links +
        # build waves) and the graph is acyclic by construction.
        task_id = str(it.get("id") or title).strip()
        deps: list[str] = []
        for d in (str(x).strip() for x in (it.get("depends_on") or []) if str(x).strip()):
            dep_title = id_to_title.get(d, d)  # map id→title; if already a title, keep as-is
            if dep_title in seen_titles and dep_title != title:
                deps.append(dep_title)
        skills = [str(s).strip() for s in (it.get("skills") or []) if str(s).strip()]
        produces = str(it.get("produces") or "").strip()
        if produces.lower() in ("null", "none"):
            produces = ""
        acceptance = _clean_criteria(it.get("acceptance") or [])
        claimed.update(files)
        seen_titles.add(title)
        id_to_title[task_id] = title
        out.append(Subtask(title=title, files=files, instructions=instructions,
                           role=_norm_role(it.get("role", "backend")),
                           depends_on=deps, skills=skills, produces=produces, acceptance=acceptance))
    return out


async def decompose(mission: Mission, planner: LLMProvider, max_parts: int, *, attempts: int = 3) -> list[Subtask]:
    """Ask the planner to split the build into ≤ ``max_parts`` disjoint-file, ROLE-TAGGED subtasks.

    Reasoning models are flaky at emitting a clean JSON array (they truncate or wrap it in prose), so
    we RETRY a few times and take the first attempt that yields ≥2 subtasks — otherwise the caller
    falls back to a single build. A generous token budget avoids truncating the array."""
    prompt = prompts.decompose_prompt(mission, max_parts)
    best: list[Subtask] = []
    for _ in range(max(1, attempts)):
        try:
            res = await planner.complete(system=DECOMPOSE_SYSTEM, prompt=prompt,
                                         purpose="plan", max_tokens=1800)
            subs = _parse_subtasks(res.text)[:max_parts]
        except Exception:  # pragma: no cover - transient planner error → try again
            subs = []
        if len(subs) >= 2:
            return subs
        if subs:
            best = subs
    return best  # 0 or 1 subtask → caller does a single build


async def plan_tasks(mission: Mission, planner: LLMProvider, max_tasks: int, *, attempts: int = 3) -> list[Subtask]:
    """PM/architect PLAN: decompose the brief into a dependency-ordered task graph BEFORE any build.

    A superset of :func:`decompose` — same disjoint-file, role-tagged subtasks, plus ``depends_on``
    (a DAG), ``skills`` (for skill-aware assignment), and ``produces`` (the contract each task
    publishes). The planner returns a JSON object ``{"tasks": [...]}``; we reuse ``_parse_subtasks``
    on the tasks array. Falls back to :func:`decompose` if the plan doesn't yield ≥2 tasks, so the
    build always has something to work from."""
    prompt = prompts.plan_prompt(mission, max_tasks)
    best: list[Subtask] = []
    for _ in range(max(1, attempts)):
        try:
            res = await planner.complete(system=prompts.PLAN_SYSTEM, prompt=prompt,
                                         purpose="plan", max_tokens=2200)
            subs = _parse_subtasks(_tasks_array(res.text))[:max_tasks]
        except Exception:  # pragma: no cover - transient planner error → try again
            subs = []
        if len(subs) >= 2:
            return subs
        if subs:
            best = subs
    # The plan shape didn't parse into a graph — fall back to the flat disjoint-file decomposition
    # with its FULL retry budget (a free model is flaky; don't collapse to solo on one bad response).
    return best or await decompose(mission, planner, max_tasks)


def _tasks_array(text: str) -> str:
    """Pull the ``tasks`` JSON array out of the planner's ``{"non_goals":[...],"tasks":[...]}`` object
    so ``_parse_subtasks`` (which expects a bare array) can read it. Falls back to the raw text when
    there is no wrapper object, so a planner that returned a bare array still works."""
    raw = (text or "").strip()
    marker = '"tasks"'
    idx = raw.find(marker)
    if idx == -1:
        return raw
    start = raw.find("[", idx)
    if start == -1:
        return raw
    depth, i = 0, start
    for i in range(start, len(raw)):
        if raw[i] == "[":
            depth += 1
        elif raw[i] == "]":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return raw[start:]


def validate_subtasks(subtasks: list[Subtask]) -> list[str]:
    """Machine-validate the ownership map (v2 Phase 4). Returns problems (empty ⇒ provably disjoint).
    ``_parse_subtasks`` already drops overlaps during parsing, so this is the belt-and-suspenders
    check + the source of the self-correction feedback message."""
    from .ownership import validate_ownership
    tasks = [
        {"id": st.title or f"T{i+1}", "role": st.role, "owned_paths": st.files}
        for i, st in enumerate(subtasks)
    ]
    valid_roles = {"frontend", "backend", "fullstack", "devops", "qa"}
    return validate_ownership(tasks, valid_roles)


def _subtask_task(mission: Mission, st: Subtask, others: list[str] | None = None, *,
                  rework_note: str = "", cto_guidance: str = "") -> str:
    """One parallel worker's task — delegates to the central prompt library."""
    return prompts.subtask_task(mission, st, others=others, rework_note=rework_note,
                               cto_guidance=cto_guidance)


_CTO_Q_RE = re.compile(r"CTO-QUESTION:\s*(.+)", re.IGNORECASE)


def _extract_cto_question(text: str) -> str:
    """The builder's escalation question, if it raised one (a ``CTO-QUESTION:`` line), else ''."""
    m = _CTO_Q_RE.search(text or "")
    return m.group(1).strip() if m else ""


BUILD_RULES = prompts.BUILD_RULES


def _assign(subtasks: list[Subtask], specs: list[tuple]) -> list[tuple[Subtask, tuple]]:
    """Pair each subtask with the best-fit agent: prefer one whose skills cover the task's needed
    skills, then one of the task's ROLE, then any free agent (so work still gets done). A spec is
    ``(provider, name, role, [agent])`` — the optional 4th element carries the Agent (with .skills)."""
    from collections import defaultdict
    pool: dict[str, list[tuple]] = defaultdict(list)
    for spec in specs:
        pool[spec[2]].append(spec)  # spec = (provider, name, role, [agent])

    def _spec_skills(spec: tuple) -> set[str]:
        agent = spec[3] if len(spec) > 3 else None
        return {s.lower() for s in (getattr(agent, "skills", None) or [])}

    def take(role: str, needed: list[str]):
        want = {s.lower() for s in (needed or [])}
        # 1) within the right role, prefer the agent whose skills best cover the task's needs.
        if pool.get(role):
            if want:
                ranked = sorted(pool[role], key=lambda sp: len(want & _spec_skills(sp)), reverse=True)
                best = ranked[0]
                if len(want & _spec_skills(best)) > 0:
                    pool[role].remove(best)
                    return best
            return pool[role].pop(0)
        # 2) no agent of that role left → best skill-cover across any remaining agent, else any.
        remaining = [sp for r in pool for sp in pool[r]]
        if not remaining:
            return None
        if want:
            remaining.sort(key=lambda sp: len(want & _spec_skills(sp)), reverse=True)
        chosen = remaining[0]
        pool[chosen[2]].remove(chosen)
        return chosen

    out: list[tuple[Subtask, tuple]] = []
    for st in subtasks:
        spec = take(st.role, st.skills)
        if spec is None:
            break
        out.append((st, spec))
    return out


async def _ensure_readme(sb: LocalSandbox, mission: Mission, author: LLMProvider, files: list[str]) -> bool:
    """Guarantee the project has a README.md (docs-as-code: the build always ships one). Written by the
    coordinator/lead from the brief + the files that landed. Returns True if it created one."""
    if any(f.lower() == "readme.md" for f in files):
        return False
    prompt = prompts.readme_prompt(mission, files)
    try:
        res = await author.complete(system=prompts.README_SYSTEM, prompt=prompt, max_tokens=900)
        content = (res.text or "").strip()
        if not content:
            return False
        await sb.write_file("README.md", content + "\n")
        await sb.commit_all(f"{mission.key}: add README")
        return True
    except Exception:  # pragma: no cover - README is best-effort; never fail the build over it
        return False


def _dependency_waves(assignments: list[tuple]) -> list[list[tuple]]:
    """Order (subtask, spec) pairs into dependency WAVES for a coherent parallel build.

    Wave 0 = tasks with no in-batch dependency; wave k = tasks whose every dependency sits in an
    earlier wave. Tasks WITHIN a wave are independent and run in parallel; each wave is merged into
    main BEFORE the next wave starts — so a later agent's worktree already contains the earlier
    agents' real code (the actual API contract, shared types, conventions) to read and build on,
    instead of guessing. ``depends_on`` holds task TITLES (see :func:`_parse_subtasks`)."""
    titles = {st.title for st, _spec in assignments}
    level: dict[str, int] = {}
    for st, _spec in assignments:  # deps point backward (parse guarantees) → a single pass suffices
        deps = [d for d in (st.depends_on or []) if d in titles and d != st.title]
        level[st.title] = (max(level.get(d, 0) for d in deps) + 1) if deps else 0
    by_level: dict[int, list] = {}
    for pair in assignments:
        by_level.setdefault(level[pair[0].title], []).append(pair)
    return [by_level[k] for k in sorted(by_level)]


async def build_parallel(
    mission: Mission, planner: LLMProvider, agents: list[tuple], *,
    subtasks: list[Subtask] | None = None,
    projects_root: str = "", make_callbacks=None, max_steps: int = 16,
    allow_solo_fallback: bool = True, rework_note: str = "", cto_consult=None,
) -> tuple[BuildResult, LocalSandbox, str]:
    """FORK-JOIN build: decompose into disjoint-file, ROLE-TAGGED subtasks, run each in its OWN git
    worktree with a SEPARATE, role-matched agent IN PARALLEL, then merge the branches and guarantee a
    README. ``agents`` is a list of ``(provider, display_name, role_key)`` — a frontend subtask goes to
    a frontend agent, backend to a backend agent. ``make_callbacks(label)`` tags each agent's console
    events. Falls back to a single-agent :func:`build_from_mission` when it can't split cleanly —
    UNLESS ``allow_solo_fallback`` is False (a TARGETED REWORK: rebuild only the given slice(s), even a
    single one, off the current codebase — never regenerate the whole app)."""
    from .roles import role_system_prompt

    # A spec is (provider, name, role, …) — callers may append extra fields (e.g. the agent object);
    # we only read [0] provider, [1] name, [2] role.
    specs = [s for s in agents if s and s[0] is not None]

    def _cbs(label: str, role: str = ""):
        return make_callbacks(label, role) if make_callbacks else (None, None)

    path = mission.project_path or default_project_path(projects_root, mission)
    sb = await LocalSandbox.at_path(path)
    if not await sb.has_repo():
        if not await sb.is_empty():
            raise RuntimeError(f"target directory is not empty: {path}")
        await sb.init_empty_repo()

    cap = min(len(specs), MAX_PARALLEL_SUBTASKS)
    # The caller may pre-decompose (to size the "on shift" team + note accurately); else do it here.
    if subtasks is None:
        subtasks = await decompose(mission, planner, cap) if cap >= 2 else []
    if allow_solo_fallback and (len(subtasks) < 2 or len(specs) < 2):
        # Not worth parallelising — do the normal single build (identical behaviour to non-parallel).
        prov, name = (specs[0][0], specs[0][1]) if specs else (planner, "")
        role0 = specs[0][2] if specs else ""
        on_ev, on_tn = _cbs(str(name), role0)
        return await build_from_mission(
            mission, prov, projects_root=projects_root,
            on_event=on_ev, on_turn=on_tn, max_steps=max_steps,
        )
    if not specs:  # nothing to run (targeted rework with no assignable agent) — report the app as-is
        base = await sb.root_sha()
        return (
            BuildResult(branch="main", diff=await sb.diff(base), files=await sb.changed_files(base),
                        tests_passed=False, summary="no builder available for the targeted rework",
                        tokens_in=0, tokens_out=0, cost_cents=0, steps=0, files_written=0),
            sb, path,
        )

    subtasks = subtasks[: len(specs)]
    assignments = _assign(subtasks, specs)  # (subtask, (provider, name, role)) — role-matched
    waves = _dependency_waves(assignments)  # dependency-ordered; each wave merged before the next runs
    # Report the WHOLE app (from the repo root), so a rework cycle's QA/health grounding sees the
    # complete merged deliverable — not just this wave's incremental delta (which shrinks each rework
    # and makes QA wrongly conclude earlier files 'disappeared').
    base = await sb.root_sha()

    async def _one(st: Subtask, spec: tuple, branch: str, wt, concurrent: list[str]):
        provider, name = spec[0], spec[1]
        agent_role = spec[2] if len(spec) > 2 else st.role  # the ACTUAL agent's role (for the label)
        on_ev, on_tn = _cbs(f"{name} · {st.title}" if name else st.title, agent_role)
        # Each worker is constrained to ITS role's lane (a backend agent won't style UI, etc.).
        system = f"{role_system_prompt(st.role)}\n\n{BUILD_RULES}"
        result = await run_agent(
            provider, wt, _subtask_task(mission, st, others=concurrent, rework_note=rework_note),
            max_steps=max_steps, system=system, on_event=on_ev, on_turn=on_tn,
            owned_paths=(st.files or None),  # TOOL-LEVEL enforcement: fs_write jailed to this slice
        )
        # Escalation: a blocked worker asks the CTO instead of guessing — consult once, then re-run it
        # with the CTO's decision (bounded to one round so a loop can't form).
        question = _extract_cto_question(result.final_text)
        if question and cto_consult is not None:
            guidance = await cto_consult(question, st.title, agent_role)
            if guidance:
                result = await run_agent(
                    provider, wt,
                    _subtask_task(mission, st, others=concurrent, rework_note=rework_note,
                                  cto_guidance=guidance),
                    max_steps=max_steps, system=system, on_event=on_ev, on_turn=on_tn,
                    owned_paths=(st.files or None),
                )
        await wt.commit_all(f"{mission.key}: {st.title}")
        return branch, wt, result

    # Run WAVE BY WAVE: create a wave's worktrees off the CURRENT main (which already holds every
    # earlier wave's merged code), run that wave's agents in parallel, then MERGE the wave into main
    # before starting the next — so later agents read and build on real upstream code, not guesses.
    merged, skipped, failed, ti, to, cc, steps, fw = [], [], 0, 0, 0, 0, 0, 0
    all_passed = True
    seq = 0
    for wave in waves:
        # Worktrees created SEQUENTIALLY (concurrent `git worktree add` contends on git's global locks).
        prepared: list[tuple] = []  # (st, spec, branch, worktree)
        for st, spec in wave:
            seq += 1
            branch = f"parallel/{mission.key.lower()}-{seq}-{_slug(st.title)}"
            wt = await sb.add_worktree(branch)  # branches off main's CURRENT HEAD (prior waves merged)
            prepared.append((st, spec, branch, wt))
        # "Concurrent" = files being written by OTHER agents in THIS wave (not yet visible). Earlier
        # waves' files are already merged into the worktree, so agents just fs_read them.
        wave_files = {f for st, _spec in wave for f in st.files}
        outcomes = await asyncio.gather(*[
            _one(st, spec, branch, wt, sorted(wave_files - set(st.files)))
            for (st, spec, branch, wt) in prepared
        ], return_exceptions=True)
        # Merge this wave into main before the next wave starts.
        for prep, item in zip(prepared, outcomes, strict=False):
            wt = prep[3]
            if isinstance(item, Exception) or item is None:
                all_passed = False
                failed += 1
                with contextlib.suppress(Exception):
                    await sb.remove_worktree_path(wt.dir)
                continue
            branch, wt, result = item
            ti += result.tokens_in
            to += result.tokens_out
            cc += result.cost_cents
            steps += result.steps
            fw += result.files_written
            all_passed = all_passed and result.tests_passed
            ok, _out = await sb.merge_branch(branch)
            (merged if ok else skipped).append(branch)
            await sb.remove_worktree_path(wt.dir)

    if not merged and allow_solo_fallback:
        # NOTHING merged — every worker crashed or every branch conflicted. There is no real work to
        # ship. Rather than return a scaffold stub (the M-157 failure), fall back to a single-agent
        # build that actually does the work, so the deliverable is real or fails loudly.
        prov, name = specs[0][0], specs[0][1]
        role0 = specs[0][2] if len(specs[0]) > 2 else ""
        on_ev, on_tn = _cbs(str(name), role0)
        return await build_from_mission(
            mission, prov, projects_root=projects_root,
            on_event=on_ev, on_turn=on_tn, max_steps=max_steps,
        )
    # (Targeted rework, no solo fallback: if nothing merged the existing app is unchanged — reported
    # below from root, marked not-passed via `failed`/`skipped`, so QA's no-progress guard can act.)

    files = await sb.changed_files(base)
    # Docs-as-code: the build always ships a README (the parallel split can otherwise omit it).
    if await _ensure_readme(sb, mission, planner, files):
        files = await sb.changed_files(base)
    diff = await sb.diff(base)
    parts = f"{len(merged)} subtask(s) merged in parallel"
    if skipped:
        parts += f"; {len(skipped)} skipped due to a merge conflict ({', '.join(skipped)})"
    if failed:
        parts += f"; {failed} worker(s) failed"
    # A build that lost any subtask (to a conflict or a crash) is NOT clean — mark tests_passed False
    # and let the engine's build-health gate + QA catch it, so a partial result can't sail through.
    return (
        BuildResult(
            branch="main", diff=diff, files=files, tests_passed=all_passed and not skipped and not failed,
            summary=parts, tokens_in=ti, tokens_out=to, cost_cents=cc, steps=steps, files_written=fw,
        ),
        sb,
        path,
    )


CHANGE_SYSTEM = prompts.CHANGE_SYSTEM


async def change_in_repo(
    mission: Mission, provider: LLMProvider, *, on_event: OnEvent | None = None,
    on_turn: OnTurn | None = None, max_steps: int = 16,
) -> tuple[BuildResult, LocalSandbox, str]:
    """Make a real, mission-driven change inside an existing repo at ``mission.project_path``,
    on a new ``fix/<key>-<slug>`` branch, and capture the diff."""
    path = mission.project_path or ""
    sb = await LocalSandbox.at_path(path)
    if not await sb.has_repo():
        raise RuntimeError(f"not a git repository: {path or '(no path)'}")
    branch = f"fix/{mission.key}-{_slug(mission.title)}"
    await sb.checkout_branch(branch)
    base = await sb.head_sha()

    task = (
        f"Ticket: {mission.title}\n\n"
        f"Details:\n{(mission.requirements or mission.summary or '').strip() or '(see title)'}\n\n"
        "Make this change in the existing codebase. Read before you edit."
    )
    result = await run_agent(
        provider, sb, task, max_steps=max_steps, system=CHANGE_SYSTEM,
        on_event=on_event, on_turn=on_turn,
    )

    await sb.commit_all(f"{mission.key}: {mission.title}")
    diff = await sb.diff(base)
    files = await sb.changed_files(base)
    summary = result.final_text.strip() or "; ".join(result.tool_log[-4:])
    return (
        BuildResult(
            branch=branch, diff=diff, files=files, tests_passed=result.tests_passed,
            summary=summary, tokens_in=result.tokens_in, tokens_out=result.tokens_out,
            cost_cents=result.cost_cents, steps=result.steps, files_written=result.files_written,
        ),
        sb,
        path,
    )


async def open_pr(sb: LocalSandbox, mission: Mission, branch: str, connector: GitHubConnector) -> PullRequestResult:
    title = f"{mission.key}: {mission.title}"
    body = (
        "Opened automatically by Shipwright.\n\n"
        f"Mission **{mission.key}** ({mission.ext_ref or 'n/a'}).\n\n"
        f"{mission.summary or ''}"
    )
    return await connector.open_pull_request(
        sandbox_dir=str(sb.dir), branch=branch, title=title, body=body
    )
