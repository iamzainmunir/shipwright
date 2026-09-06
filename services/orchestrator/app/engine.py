"""The mission-run engine (Phase 1, in-process).

Executes a Mission through the run phases (Canon §6), creating Steps and Events as the AI
team works, calling the LLM provider for the reasoning phases, and metering tokens/cost onto
the Run. At the **ship** phase, under any non-autonomous autonomy, it raises an **approval
blocker** and SUSPENDS the run until a human resolves it — mirroring the Temporal
``wait_condition`` + signal pattern (now superseded by graph-engine interrupts). Resolve →
resume → shipped.

Same activities/steps are reused by the Temporal workflow; this in-process variant is the
default so the slice runs with no Temporal server.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from foundry_core import tracing
from foundry_core.enums import (
    AgentRoleKey,
    AgentStatus,
    ApprovalDecision,
    ApprovalGate,
    AutonomyLevel,
    BlockerKind,
    BlockerSeverity,
    ConnectionStatus,
    MissionStage,
    RunStatus,
    StepStatus,
)
from foundry_core.ids import new_ulid
from foundry_core.models import Approval, Blocker, Event, Mission, Run, Step

from .events import EventBus
from .prompts import ROLE_SYSTEM_FALLBACK as _SYSTEM
from .prompts import VERDICT_ASK as _VERDICT_ASK
from .providers import LLMProvider, ProviderError
from .store import InMemoryStore


def _now() -> datetime:
    return datetime.now(UTC)


def _dir_exists(path: str) -> bool:
    """Sync filesystem check, isolated here so async callers stay off the pathlib/os.path lint."""
    from pathlib import Path
    return Path(path).is_dir()


def _enum_value(value: object) -> str | None:
    """Enum → its string value; passes plain strings through (for span attributes)."""
    if value is None:
        return None
    return getattr(value, "value", value)  # type: ignore[return-value]


@dataclass(slots=True)
class Phase:
    key: str          # Canon §6 run phase
    title: str
    role: AgentRoleKey
    event_type: str   # Canon §7 event type
    purpose: str      # provider hint; "" = no LLM call (pure orchestration/gate)
    stage: MissionStage


PHASES: list[Phase] = [
    Phase("intake", "Read ticket & frame the work", AgentRoleKey.PM, "spec", "intake", MissionStage.SPEC),
    Phase("clarify", "Ask clarifying questions", AgentRoleKey.PM, "", "clarify", MissionStage.SPEC),
    Phase("spec", "Draft the spec & acceptance stories", AgentRoleKey.PM, "spec", "spec", MissionStage.SPEC),
    # PM breaks the spec into a dependency-ordered task graph and creates the tickets BEFORE any code
    # is written. purpose="" ⇒ orchestration only (no generic LLM phase); handled in _run_phase.
    Phase("plan", "Decompose into a task graph & tickets", AgentRoleKey.PM, "spec", "", MissionStage.SPEC),
    Phase("build.api", "Implement the change (TDD)", AgentRoleKey.BACKEND, "code", "code", MissionStage.BUILDING),
    # Code review comes BEFORE QA: the CTO reviews the code first, then QA verifies the reviewed
    # build's behaviour as the final gate before ship (so what ships is exactly what QA blessed).
    Phase("review", "Code review", AgentRoleKey.CTO, "review", "review", MissionStage.REVIEW),
    Phase("qa", "Run acceptance checks", AgentRoleKey.QA, "qa", "qa", MissionStage.QA),
    Phase("ship", "Merge & deploy", AgentRoleKey.DEVOPS, "deploy", "", MissionStage.REVIEW),
]

# Role personas (_SYSTEM) and verdict asks (_VERDICT_ASK) now live in app/prompts.py, imported above.

# ── Non-linear pipeline ──────────────────────────────────────────────────────────────────────
# The run is a DECISION GRAPH, not a straight line: a phase's verdict can send the work forward,
# back to an earlier phase, or escalate to the CTO (Canon: work flows in any direction, not only
# forward). Concretely — build↔QA rework loop, review→Backend/Spec on changes, review→CTO for a
# consultation, and the CTO can redesign (→spec, "back to step one"), rebuild (→build), or proceed
# (→ship). ``_next_phase`` encodes the edges; ``MAX_*`` caps keep any loop finite.

# The CTO consultation phase — NOT on the default forward path; reached only when review escalates
# a decision. From here the CTO chooses the direction the whole project takes next.
CTO_DECISION = Phase(
    "cto.decision", "CTO decision — consult & choose direction",
    AgentRoleKey.CTO, "review", "decision", MissionStage.REVIEW,
)

# Canonical forward order, used only for a MONOTONIC progress bar (routing is the graph below).
CANON_ORDER = ["intake", "clarify", "spec", "plan", "build.api", "review", "qa", "ship"]
_PHASE_BY_KEY: dict[str, Phase] = {p.key: p for p in [*PHASES, CTO_DECISION]}

_DIFF_BUDGET = 20000    # chars of the real diff shown to QA/review/CTO grounding (fits a small app)
MAX_REWORK_CYCLES = 3   # build↔QA and review→Backend reworks before forcing forward
MAX_ESCALATIONS = 2     # review→CTO consultations before the CTO must pick a direction
MAX_ERROR_ESCALATIONS = 2  # CTO-guided retries of a FAILED phase before the user is involved
MAX_PUSH_REGATES = 3    # times the merge gate re-opens after a rejected push before giving up
_PIPELINE_SAFETY_STOP = 40  # absolute backstop on total phase executions (never expected to hit)
MAX_CTO_DECISIONS = 2   # CTO consultations before the work is escalated to the user (a real cap)
# Routing sentinel: STOP the pipeline and hand the mission to the user (a fail-safe block), instead of
# forcing an unverified build forward to ship when automated rework/escalation budgets are exhausted.
_HALT = "__halt__"


@dataclass(slots=True)
class Outcome:
    """A phase's verdict, which drives routing. ``token`` is the normalized decision word
    (PASS/REWORK/APPROVE/ESCALATE/PROCEED/REDESIGN/REBUILD/ADVANCE/APPROVE_GATE/REJECT_GATE)."""

    token: str
    reason: str = ""


# What each decision phase must end its reply with (_VERDICT_ASK) is defined in app/prompts.py,
# imported above — the tokens map 1:1 onto the edges in ``_next_phase``.

# Valid verdict tokens per decision phase. The FORWARD (ship-ward) token is always FIRST.
_VERDICT_VALID: dict[str, tuple[str, ...]] = {
    "qa": ("PASS", "REWORK", "PARTIAL"),  # PARTIAL: core criteria pass, a minor portion is deferred
    "review": ("APPROVE", "REWORK", "ESCALATE"),
    "cto.decision": ("PROCEED", "REDESIGN", "REBUILD"),
}
# FAIL-SAFE fallback per decision phase: when a verdict is present but can't be recognised, HOLD the
# work (send it back) rather than shipping it. This is the opposite of the old fail-forward default,
# which read a truncated/odd reply as PASS and shipped unverified work (the M-157 bug). Loop caps keep
# this finite, and past the cap the run BLOCKS for the user instead of auto-advancing (see _next_phase),
# so failing safe never loops forever.
_VERDICT_FALLBACK: dict[str, str] = {"qa": "REWORK", "review": "REWORK", "cto.decision": "REDESIGN"}
# Sentinel: NO verdict line was found at all (vs. one found but unrecognised). Lets the caller re-elicit
# the verdict with a focused follow-up call before falling back — so body truncation is harmless.
_NO_VERDICT = "__NONE__"
# Plain-English negatives a model naturally writes instead of the exact token → treated as "hold".
_NEGATIVE_HOLD = (
    "REWORK", "FAIL", "REJECT", "BLOCK", "NEEDS", "INSUFFICIENT", "INCOMPLETE", "INCORRECT",
    "BROKEN", "MISSING", "STUB", "PLACEHOLDER", "DOES NOT MEET", "NOT MEET", "NOT READY", "NOT PASS",
)
# Affirmatives that map to the phase's forward token.
_AFFIRM = ("PASS", "APPROVE", "PROCEED", "LGTM", "SHIP IT", "READY TO MERGE", "MEETS", "ACCEPTED")
# Tolerant of the markdown a model actually emits — "**Verdict:** REWORK", "`VERDICT`: PASS" — and of
# multi-word verdicts ("DOES NOT MEET", "NEEDS WORK") so a real REWORK is never read as the PASS default.
_VERDICT_RE = re.compile(
    r"VERDICT[\s*_`]*:[\s*_`]*([A-Za-z][A-Za-z _'-]*?)\s*(?:[—:\-]\s+(.*))?$",
    re.IGNORECASE | re.MULTILINE,
)


_QUESTION_WORDS = (
    "what", "which", "who", "whom", "whose", "how", "should", "shall", "do", "does", "did",
    "is", "are", "was", "were", "can", "could", "would", "will", "where", "when", "why",
)


def _extract_questions(text: str, *, limit: int = 4) -> list[str]:
    """Pull clarifying questions from a model reply, tolerant of weak local models.

    Accepts lines that end with '?' OR read as a question (start with a question word), after
    stripping list markers/numbering. Returns [] when the model signals the brief is clear (NONE).
    """
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = re.sub(r"^[\-\*\d\.\)\s]+", "", raw).strip()
        if not line:
            continue
        if line.upper() == "NONE":
            return []
        first = line.split()[0].lower().rstrip(":,") if line.split() else ""
        looks_like_q = line.endswith("?") or first in _QUESTION_WORDS
        if looks_like_q:
            out.append(line if line.endswith("?") else line.rstrip(".") + "?")
        if len(out) >= limit:
            break
    return out


def _normalize_verdict(phase_key: str, raw: str, reason: str) -> str:
    """Map a parsed verdict phrase to a valid token for the phase. Exact tokens win; otherwise a
    plain-English negative (FAIL/REJECT/NEEDS…) HOLDS the work (fail-safe), an affirmative advances,
    and anything still unrecognised HOLDS — never silently advances."""
    valid = _VERDICT_VALID[phase_key]
    word = raw.upper().split()[0] if raw.split() else ""
    if word in valid:
        return word
    blob = f"{raw} {reason}".upper()
    # Negatives take precedence over affirmatives (a reason may mention the word "pass" while failing).
    if any(neg in blob for neg in _NEGATIVE_HOLD):
        return _VERDICT_FALLBACK[phase_key]
    if any(pos in blob for pos in _AFFIRM):
        return valid[0]  # the forward (ship-ward) token
    return _VERDICT_FALLBACK[phase_key]  # present but unrecognised → fail safe (hold), never advance


MAJOR_RATIO = 0.5  # ≥ this share of machine-checked criteria failing ⇒ reopen (major), never forward
_SMOKE_FLOOR_IDS = ("load", "render")  # blocking infra checks; console-error checks match by name


def _classify_qa(evidence: dict, token: str, reason: str, healthy: bool) -> str:
    """Reduce (LLM verdict token + ground-truth evidence) → PASS | REWORK | PARTIAL. First match wins.

    Conservative by construction — an unhealthy build, a smoke-floor failure, a major share of criteria
    failing, or any BLOCKING criterion failing all force REWORK, and anything ambiguous defaults to
    REWORK. The LLM never has the last word, so a mislabelled 'PARTIAL' can't ship a broken build.
    (Steps 1-3 only bite for criteria carrying machine checks; a prose-only spec rests PARTIAL on the
    token, bounded by step 0 (health) + step 5 corroboration + the blocking-by-default severity.)"""
    ev = evidence or {}
    checks = ev.get("checks") or []
    # 0. ground-truth gate — an unhealthy build NEVER forwards
    if not healthy:
        return "REWORK"
    # 1. smoke-floor: a load/render/console failure is a blocking infrastructure failure
    for c in checks:
        cid = c.get("id") or ""
        if c.get("status") == "fail" and (cid in _SMOKE_FLOOR_IDS or "console" in (c.get("name") or "").lower()):
            return "REWORK"
    # 2. major-ratio over machine-checked criteria (warn = soft pass, not counted as a fail)
    total = ev.get("crit_total") or 0
    failed = ev.get("crit_failed") or 0
    if total > 0 and (failed / total) >= MAJOR_RATIO:
        return "REWORK"
    # 3. any BLOCKING-severity criterion failing ⇒ reopen
    for c in checks:
        if c.get("criterion_id") and c.get("status") == "fail" and (c.get("severity") or "blocking") == "blocking":
            return "REWORK"
    # 4. clean pass
    if token == "PASS":
        return "PASS"
    # 5. PARTIAL only when CORROBORATED by machine checks: ≥1 criterion passed AND the reason names a
    #    failing criterion. A prose-only spec (no machine criteria) can't corroborate → falls through to
    #    REWORK, so we never forward a build we couldn't actually verify.
    if token == "PARTIAL":
        crit = [c for c in checks if c.get("criterion_id")]
        any_pass = any(c.get("status") == "pass" for c in crit)
        failing_ids = [c.get("criterion_id") for c in crit if c.get("status") == "fail"]
        names_a_failure = bool(reason.strip()) and any((fid or "") in reason for fid in failing_ids)
        if any_pass and names_a_failure:
            return "PARTIAL"
    # 6. fallback — unparseable/ambiguous ⇒ hold (fail-safe)
    return "REWORK"


def _parse_verdict(phase_key: str, text: str) -> Outcome:
    """Extract a routing verdict from a decision phase's reply. Non-decision phases always ADVANCE.

    Returns ``Outcome(_NO_VERDICT)`` when NO verdict line is present at all, so the caller can re-elicit
    it (a truncated analysis that never reached the VERDICT line is thus recoverable, not read as PASS).
    A verdict that IS present but unrecognised fails safe to the phase's HOLD token, never forward."""
    if phase_key not in _VERDICT_VALID:
        return Outcome("ADVANCE")
    raw, reason = "", ""
    for m in _VERDICT_RE.finditer(text or ""):  # last verdict line wins
        raw = m.group(1).strip()
        reason = (m.group(2) or "").strip()
    if not raw:
        return Outcome(_NO_VERDICT, "")
    return Outcome(_normalize_verdict(phase_key, raw, reason), reason)


# Files that don't count as "real work" when judging whether a build actually produced something.
_SCAFFOLD_FILES = {"readme.md", "license", "license.md", ".gitignore", ".gitattributes"}


def _assess_build(result: object) -> tuple[bool, str]:
    """Objective health of a build result — the gate a hallucinated verdict cannot pass.

    A build is UNHEALTHY when it produced no real work: zero agent steps (the stub/fallback path that
    shipped an empty app in M-157), no meaningful files beyond scaffold, or a parallel merge that lost
    every subtask to conflicts. ``tests_passed`` is deliberately NOT a hard gate here because "no tests
    were run" and "tests failed" are indistinguishable in the current signal — it is surfaced to QA as
    advisory context instead. Returns (healthy, reason)."""
    files = list(getattr(result, "files", []) or [])
    steps = int(getattr(result, "steps", 0) or 0)
    summary = str(getattr(result, "summary", "") or "")
    meaningful = [f for f in files if f.rsplit("/", 1)[-1].lower() not in _SCAFFOLD_FILES]
    if "skipped due to a merge conflict" in summary:
        return False, "the parallel build lost work to merge conflicts (some subtasks were skipped)"
    if steps <= 0:
        return False, "the build agent did no real work (0 steps) — only a scaffold/stub was produced"
    # Direct signal: the agent made zero successful fs_write calls. Paired with `not meaningful` so a
    # change-in-repo that only EDITS existing files (git shows files, fs_write>0) is unaffected.
    if int(getattr(result, "files_written", 0) or 0) == 0 and not meaningful:
        return False, "the build wrote no files (0 fs_write calls) — no real work was produced"
    if not meaningful:
        return False, "the build produced only scaffold files (README/.gitignore), no real code"
    return True, ""


class RunEngine:
    def __init__(self, store: InMemoryStore, bus: EventBus, provider: LLMProvider) -> None:
        self.store = store
        self.bus = bus
        self.provider = provider
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._run_tasks: dict[str, asyncio.Task[None]] = {}  # run_id → its _execute task (for cancel)
        # Phase 3: real sandbox dev loop + GitHub PR (config-gated; safe dry-run by default).
        from .config import get_settings
        from .connectors import GitHubConnector

        s = get_settings()
        self.sandbox_enabled: bool = s.sandbox_enabled
        self.sandbox_root: str | None = s.sandbox_root or None
        self.projects_root: str = s.projects_root
        self._connector = GitHubConnector(s.github_token, s.github_repo, s.github_base)
        self._builds: dict[str, tuple[object, str]] = {}  # mission_id → (sandbox, branch)
        # Ground-truth facts about the LATEST build, so QA/review/CTO judge the REAL deliverable and
        # the graph can gate on it (never ship a build that produced no real work). See _assess_build.
        self._build_facts: dict[str, dict] = {}  # mission_id → {files, tests_passed, steps, diff, healthy, why}
        # The PM's task graph from the `plan` phase — the build REUSES it (never re-decomposes) so the
        # pre-created tickets and the build's subtasks line up. mission_id → list[devloop.Subtask].
        self._plans: dict[str, list] = {}
        # Targeted rework: mission_id → {subtask titles QA flagged as failing}. The next build rebuilds
        # ONLY those parts (off the current codebase), instead of regenerating the whole app each cycle.
        self._rework_targets: dict[str, set[str]] = {}
        # The reason QA/review/CTO sent the build back — handed to the builders as explicit fix
        # instructions so a rework is targeted at the real gap, not a blind regenerate.
        self._rework_reason: dict[str, str] = {}
        # Repo/branch the user supplies at the merge gate, so the push targets their real repo.
        self._merge_meta: dict[str, dict[str, str]] = {}  # mission_id → {repo, branch}
        # Multi-project coordinated change (Approach A): mission_id → per-repo build detail
        # [{name, path, branch, files, diff, healthy, ...}]. Empty for single-project missions.
        self._multi_repo: dict[str, list] = {}
        # Built-in ticket board consumer (plan 05) — derives Epics/Stories/Bugs from the lifecycle.
        # Failure-isolated: a ticket bug never fails a run (Observer rule). The optional Jira mirror
        # hooks in as the consumer's sink, enqueuing outbox rows only (Rule 0: Jira never blocks a run).
        from .jira_mirror import JiraMirror
        from .tickets import TicketService
        self._jira = JiraMirror(store)
        self._tickets = TicketService(store, sink=self._jira.on_ticket_event)
        from .notifier import Notifier
        self._notifier = Notifier(store)  # email/WhatsApp/Slack on blocker/ship/halt (failure-isolated)

    async def _effective_projects_root(self) -> str:
        """Where greenfield apps are built: the workspace's configured ``projects_dir`` (Settings),
        falling back to the global default (``SHIPWRIGHT_PROJECTS_ROOT`` → ``~/ShipwrightProjects``)."""
        configured = ((await self.store.get_settings()).projects_dir or "").strip()
        return configured or self.projects_root

    async def _project_targets(self, mission: Mission) -> list[tuple[str, str]]:
        """A multi-target change's projects as ``(name, path)`` — empty for a single-project mission.

        Silently drops ids that no longer resolve; the build then treats a repo that can't be
        opened as an unhealthy slice (never crashes the run)."""
        ids = list(getattr(mission, "project_ids", []) or [])
        if not ids:
            return []
        out: list[tuple[str, str]] = []
        for pid in ids:
            project = await self.store.get_project(pid, mission.workspace_id)
            if project is not None:
                out.append((project.name, project.path))
        return out

    async def _build_multi_repo(self, mission: Mission, provider, targets, on_tool, on_turn):
        """Coordinated change across several repos: each is edited on its own ``fix/`` branch with the
        WHOLE working set as shared context, so a change stays consistent across repos (e.g. add an
        API in the service and wire it into the gateway). Returns an aggregated ``(BuildResult, sb)``
        and stashes per-repo detail in ``self._multi_repo[mission.id]``."""
        from . import devloop
        from .repomap import build_context

        context = build_context(targets)
        original = (mission.requirements or mission.summary or mission.title or "").strip()
        repos: list[dict] = []
        all_files: list[str] = []
        diffs: list[str] = []
        first_sb = None
        branch = ""
        steps = files_written = tokens_in = tokens_out = cost_cents = 0
        tests_all = True
        for name, path in targets:
            brief = (
                f"You are making ONE coordinated change across {len(targets)} repositories. Here is the "
                f"full working set so you keep them consistent:\n\n{context}\n\n"
                f"Now edit ONLY this repository — {name} ({path}) — for its part of the change:\n\n"
                f"{original}\n\nIf this repo needs no change for this request, make no edits."
            )
            per = mission.model_copy(update={"project_path": path, "requirements": brief})
            try:
                result, sb, _ = await devloop.change_in_repo(
                    per, provider, on_event=on_tool, on_turn=on_turn,
                )
            except Exception as exc:  # noqa: BLE001 — one repo failing is recorded; others continue
                repos.append({"name": name, "path": path, "branch": "", "files": [],
                              "tests_passed": False, "diff": "", "healthy": False, "error": str(exc)})
                continue
            first_sb = first_sb or sb
            branch = branch or result.branch
            healthy, _why = _assess_build(result)
            all_files += [f"{name}:{f}" for f in result.files]
            diffs.append(f"# {name} ({path}) — branch {result.branch}\n{result.diff}")
            steps += result.steps
            files_written += result.files_written
            tokens_in += result.tokens_in
            tokens_out += result.tokens_out
            cost_cents += result.cost_cents
            tests_all = tests_all and result.tests_passed
            repos.append({"name": name, "path": path, "branch": result.branch,
                          "files": list(result.files), "tests_passed": result.tests_passed,
                          "diff": result.diff, "healthy": healthy, "summary": result.summary})
        self._multi_repo[mission.id] = repos
        edited = [r["name"] for r in repos if r["files"]]
        agg = devloop.BuildResult(
            branch=branch or f"fix/{mission.key}-multi", diff="\n\n".join(diffs), files=all_files,
            tests_passed=tests_all, steps=steps, files_written=files_written,
            tokens_in=tokens_in, tokens_out=tokens_out, cost_cents=cost_cents,
            summary=(f"Coordinated change across {len(repos)} repos"
                     + (f"; edited {', '.join(edited)}" if edited else "; no edits were needed")),
        )
        return agg, first_sb

    async def _github_token(self) -> str:
        """The GitHub push token. Prefers the token the user connected on the Integrations page
        (persisted in the DB, so it survives restarts), falling back to the server env token."""
        try:
            for integ in await self.store.list_integrations():
                if integ.kind == "github" and _enum_value(integ.status) == "connected":
                    tok = str((integ.config or {}).get("token") or "").strip()
                    if tok:
                        return tok
        except Exception:  # pragma: no cover - never let token lookup break the push path
            pass
        return self._connector.token

    async def _connector_for(self, repo: str | None, force: bool = False):
        """A connector targeting the user-supplied repo. The token comes from the connected GitHub
        integration (else server config); base stays from server config. ``force`` is a per-approval
        USER decision (never autonomous) — it force-pushes the CHOSEN branch."""
        from .connectors import GitHubConnector

        token = await self._github_token()
        return GitHubConnector(token, (repo or self._connector.repo),
                               self._connector.base, force=force)

    # ---- public API -------------------------------------------------------------
    async def _supersede_active_runs(self, mission: Mission) -> None:
        """Cancel any prior non-terminal run for this mission before a re-run starts, so a fresh run
        never collides with a stale one. Critically this HARD-CANCELS the run's asyncio task (a run
        mid-phase has no pending future, so cancelling a future alone would leave a zombie coroutine
        that keeps writing shared mission state and could raise a second gate). It also resolves the
        run's open blockers, cancels any gate/clarify future, and clears a GATED ship step so a late
        approval can't finalize the dead run."""
        ws = mission.workspace_id
        runs = [r for r in await self.store.list_runs(ws) if r.mission_id == mission.id]
        stale = [r for r in runs if _enum_value(r.status) in ("running", "blocked", "queued", "paused")]
        for r in stale:
            # Stop the live coroutine (mid-phase runs are only stoppable this way).
            task = self._run_tasks.pop(r.id, None)
            if task is not None and not task.done():
                task.cancel()
            await self.store.update_run(r.id, status=RunStatus.CANCELLED, finished_at=_now())
            # A GATED ship step would otherwise still match _gate_run — clear it so an approval
            # can't resurrect this cancelled run.
            for s in await self.store.list_steps(r.id):
                if _enum_value(s.status) in ("active", "gated"):
                    await self.store.update_step(s.id, status=StepStatus.BLOCKED)
        # Resolve the mission's open blockers and unblock any coroutine still suspended on them.
        for b in await self.store.list_blockers(ws, unresolved_only=True):
            if b.mission_id != mission.id:
                continue
            await self.store.resolve_blocker_record(b.id, resolved_by="superseded")
            fut = self._pending.pop(b.id, None)
            if fut is not None and not fut.done():
                fut.cancel()
        if stale:
            await self._reset_agents_idle(ws)
            await self.store.update_mission(mission.id, is_blocked=False)

    async def start_run(self, mission: Mission, *, start_phase: str = "intake") -> Run:
        """Create a Run and kick off execution in the background; return immediately.

        ``start_phase`` lets a retry resume from a checkpoint (skipping already-done phases) while
        the mission's accumulated context (requirements + on-disk project + prior outputs) carries
        forward."""
        await self._supersede_active_runs(mission)
        run = Run(
            id=new_ulid(),
            mission_id=mission.id,
            workspace_id=mission.workspace_id,
            status=RunStatus.RUNNING,
            autonomy=mission.autonomy,
            started_at=_now(),
        )
        await self.store.add_run(run)
        await self.store.update_mission(mission.id, is_blocked=False)
        await self._tickets.ensure_epic(mission)  # board Epic for the mission (idempotent, isolated)
        await self._tickets.on_run_started(mission)  # a retry lifts a Blocked/Done Epic back to active
        resume = f" (resuming from {start_phase})" if start_phase != "intake" else ""
        await self._emit(run, mission, AgentRoleKey.PM, "system", f"Run started for {mission.key}{resume}")
        task = asyncio.create_task(self._execute(run.id, mission.id, start_phase=start_phase))
        self._tasks.add(task)
        self._run_tasks[run.id] = task

        def _done(t: asyncio.Task[None], run_id: str = run.id) -> None:
            self._tasks.discard(t)
            self._run_tasks.pop(run_id, None)

        task.add_done_callback(_done)
        return run

    async def request_change(self, mission: Mission, request: str, *, actor: str | None = None) -> Run:
        """Reopen a mission with a new change request and run it again — the org keeps iterating
        even after a project shipped (Canon: a project is never permanently 'done'). The request is
        folded into the brief so the team builds ON TOP of what already exists."""
        base = (mission.requirements or mission.summary or "").strip()
        stamped = f"{base}\n\nChange request:\n{request.strip()}".strip()
        # An app that already exists on disk keeps building in place; the graph handles the rest.
        updated = await self.store.update_mission(
            mission.id, requirements=stamped, stage=MissionStage.BACKLOG,
            progress=0, is_blocked=False, pr_url=None,
        )
        await self._tickets.on_change_request(updated, request)  # board: reopen Epic + Stories (isolated)
        # Emit on the (now-terminal) latest run's id if any, else a lightweight standalone note is
        # skipped — start_run will announce the new run immediately after.
        return await self.start_run(updated)

    async def cancel_run(self, mission: Mission, *, actor: str | None = None) -> int:
        """Force-stop the mission's active run (user-initiated). Reuses the safe teardown that
        supersede uses (hard-cancel the task, resolve blockers, reset agents, unwind gated steps).
        The mission's context + progress remain in the DB so a later retry can resume. Returns how
        many runs were stopped."""
        runs = [r for r in await self.store.list_runs(mission.workspace_id) if r.mission_id == mission.id]
        active = [r for r in runs if _enum_value(r.status) in ("running", "blocked", "queued", "paused")]
        latest_id = active[-1].id if active else None
        await self._supersede_active_runs(mission)
        # Move the mission OUT of its in-flight stage (e.g. "building") to STOPPED, so the board and
        # dashboard stop counting it as actively building. Retry resumes it. Never touch a shipped one.
        m0 = await self.store.get_mission(mission.id)
        if m0 is not None and m0.stage is not MissionStage.SHIPPED:
            stopped = await self.store.update_mission(
                mission.id, stage=MissionStage.STOPPED, is_blocked=False)
            await self._tickets.on_cancelled(stopped)  # board: Epic + open Stories → Blocked (isolated)
        if latest_id:
            run = await self.store.get_run(latest_id)
            m = await self.store.get_mission(mission.id)
            if run and m:
                await self._emit(run, m, AgentRoleKey.CEO, "system",
                                 "⛔ Run force-stopped — progress saved; retry to resume",
                                 payload={"kind": "run.cancelled"})
        return len(active)

    async def _resume_phase(self, mission: Mission) -> str:
        """Where a retry should pick up: the phase AFTER the furthest canonical phase that completed
        ON THE MOST RECENT RUN only (so finished work isn't redone). It must NOT fall through to an
        older run's progress — after a reopen/change request, an older run that already reached
        review/ship would wrongly make the retry skip the build the NEW change needs. Falls back to
        intake when the latest run has no completed phases."""
        runs = [r for r in await self.store.list_runs(mission.workspace_id) if r.mission_id == mission.id]
        runs.sort(key=lambda r: str(r.started_at or ""))
        if not runs:
            return "intake"
        latest = runs[-1]
        steps = await self.store.list_steps(latest.id)
        # A step is DONE even when its verdict was REWORK (qa) / not-APPROVE (review). Only treat a
        # decision phase as a PASSED checkpoint when its recorded verdict actually passed — otherwise
        # resume AT that phase so unverified work is re-checked instead of skipped straight to ship.
        _passed = {"qa": ("PASS",), "review": ("APPROVE",)}
        done: list[str] = []
        for s in steps:
            if _enum_value(s.status) != "done" or s.phase not in CANON_ORDER:
                continue
            need = _passed.get(s.phase)
            if need is not None:
                detail = (s.detail or "")
                token = detail.split("verdict:", 1)[1].strip() if "verdict:" in detail else ""
                if token not in need:
                    continue  # decision step did not pass → not a valid checkpoint
            done.append(s.phase)
        if not done:
            return "intake"
        idx = max(CANON_ORDER.index(p) for p in done)
        return CANON_ORDER[min(idx + 1, len(CANON_ORDER) - 1)]

    async def retry_run(self, mission: Mission, *, from_start: bool = False) -> Run:
        """Retry the mission, preserving context (requirements + on-disk project + prior outputs).
        By default it RESUMES from the checkpoint (the phase after the last completed one); pass
        ``from_start`` to redo the whole pipeline. Combine with changing the model first to
        'retry on a new model with the same context/progress'."""
        start = "intake" if from_start else await self._resume_phase(mission)
        return await self.start_run(mission, start_phase=start)

    async def resolve_blocker(
        self, blocker_id: str, decision: ApprovalDecision, *, actor: str | None, note: str | None,
        repo: str | None = None, branch: str | None = None, force: bool = False,
    ) -> Blocker:
        blocker = await self.store.get_blocker(blocker_id)
        if blocker is None:
            raise KeyError(blocker_id)
        # Already resolved (e.g. superseded by a re-run, or a double-submit)? Do nothing — never
        # finalize on top of a resolved gate, which could ship a dead run.
        if blocker.resolved_at is not None:
            return blocker
        # Stash the user's repo/branch/force choice so the merge push targets their real repo.
        # `force` is the USER explicitly authorizing an overwrite of the base branch (never the AI).
        if repo or branch or force:
            self._merge_meta[blocker.mission_id] = {
                "repo": repo or "", "branch": branch or "", "force": force,
            }
        resolved = await self.store.resolve_blocker_record(blocker_id, resolved_by=actor)
        if blocker.kind is BlockerKind.APPROVAL or blocker.kind == "approval":
            await self._record_approval(blocker, decision, actor, note)
        fut = self._pending.pop(blocker_id, None)
        if fut is not None and not fut.done():
            fut.set_result(decision.value if isinstance(decision, ApprovalDecision) else str(decision))
        elif blocker.kind is BlockerKind.APPROVAL or blocker.kind == "approval":
            # No suspended coroutine to resume (e.g. the process restarted since the gate was
            # raised) — finalize the mission directly so an approval is never a silent no-op.
            # The durable Temporal engine handles this via signals; this keeps the in-process
            # engine correct across restarts.
            await self._finalize_gate(blocker.mission_id, decision)
        return resolved

    # ---- clarifying questions (app-builder intake) ------------------------------
    async def _generate_questions(self, mission: Mission, provider) -> list[str]:
        """Ask the model for a few clarifying questions about the brief. Returns [] if it's clear."""
        brief = (mission.requirements or mission.summary or mission.title).strip()
        prompt = (
            f"A user asked to build this:\n{mission.title}\n{brief}\n\n"
            "List up to 4 short, specific questions you must have answered before building "
            "(scope, stack, features, data). One question per line, no numbering, no preamble. "
            "If the brief is already clear enough to start, reply with exactly: NONE"
        )
        try:
            result = await provider.complete(
                system="You are a product manager scoping a build. Be concise.",
                prompt=prompt, purpose="clarify", max_tokens=300,
            )
        except ProviderError:
            return []
        return _extract_questions(result.text)

    async def _clarify_gate(self, run_id: str, mission_id: str, step_id: str) -> None:
        """For app missions (non-autonomous): ask clarifying questions, suspend until answered,
        then fold the answers into the mission's requirements so the build uses them."""
        mission = await self.store.get_mission(mission_id)
        run = await self.store.get_run(run_id)
        if mission is None or run is None:
            return
        if _enum_value(mission.project_kind) != "app":
            return
        if mission.autonomy is AutonomyLevel.AUTONOMOUS or mission.autonomy == "autonomous":
            return

        provider = await self._active_provider()
        questions = await self._generate_questions(mission, provider)
        if not questions:
            return

        blocker = Blocker(
            id=new_ulid(), org_id=mission.org_id, workspace_id=mission.workspace_id,
            mission_id=mission.id, kind=BlockerKind.QUESTION, severity=BlockerSeverity.WARN,
            detail=json.dumps({"questions": questions}), created_at=_now(),
        )
        await self.store.add_blocker(blocker)
        await self._notifier.on_blocker(blocker, mission)
        await self.store.update_step(step_id, status=StepStatus.GATED)
        await self.store.update_run(run_id, status=RunStatus.BLOCKED)
        await self.store.update_mission(mission_id, is_blocked=True)
        await self._emit(
            run, mission, AgentRoleKey.PM, "clarify", "⏸ A few questions before I build",
            payload={"kind": "clarify.awaiting", "blockerId": blocker.id, "questions": questions},
        )

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending[blocker.id] = fut
        answers_raw = await fut  # ← suspended until submit_clarification fires

        await self.store.update_run(run_id, status=RunStatus.RUNNING)
        await self.store.update_mission(mission_id, is_blocked=False)
        try:
            answers = json.loads(answers_raw) if isinstance(answers_raw, str) else (answers_raw or [])
        except (json.JSONDecodeError, TypeError):
            answers = []
        answered = [a for a in answers if str(a.get("answer", "")).strip()]
        if answered:
            qa = "\n".join(f"Q: {a.get('question', '')}\nA: {a.get('answer', '')}" for a in answered)
            m = await self.store.get_mission(mission_id) or mission
            base = (m.requirements or m.summary or "").strip()
            merged = f"{base}\n\nClarifications:\n{qa}".strip()
            await self.store.update_mission(mission_id, requirements=merged)
            await self._emit(run, await self.store.get_mission(mission_id) or mission,
                             AgentRoleKey.PM, "clarify",
                             "Clarifications received — continuing the build",
                             payload={"kind": "clarify.answered"})

    async def _plan_gate(self, run_id: str, mission_id: str, step_id: str) -> None:
        """PM planning phase: decompose the spec into a dependency-ordered task graph ONCE, cache it
        for the build to reuse, and create the Stories UP FRONT (in To Do, with dependency links).

        Coherence rule (why this mirrors _real_build's own decision): the build only fans out into
        multiple subtasks when it will actually run in PARALLEL. If we pre-created multiple stories but
        the build then ran SOLO (greenfield default), those stories would be orphaned and the solo
        build would make its own separate 'build' story. So we plan multiple stories ONLY when the
        build will parallelise (same predicate + same builder-count cap the build uses); otherwise we
        create exactly one 'build'-keyed story matching the solo build's subtask_id."""
        mission = await self.store.get_mission(mission_id)
        run = await self.store.get_run(run_id)
        if mission is None or run is None:
            return
        from . import devloop
        kind = _enum_value(mission.project_kind)
        will_parallel = kind == "app" and self._wants_parallel(mission) and not self._is_solo(mission)
        tasks: list = []
        if will_parallel:
            # cap must equal the build's cap → count the SAME builder specs the build will recruit.
            n_specs = 0
            for br in (AgentRoleKey.BACKEND, AgentRoleKey.FRONTEND):
                n_specs += len(await self._team_agents_for(mission, br))
            cap = min(n_specs, devloop.MAX_PARALLEL_SUBTASKS)
            if cap >= 2:
                # Fail OVER across the CTO's whole model chain — decomposing into a clean JSON DAG needs
                # a capable model, and the first one may be rate-limited (free tiers) or a weak local
                # model. Take the first provider that yields a real ≥2-task plan.
                for planner in await self._resolve_providers(mission, AgentRoleKey.CTO):
                    with contextlib.suppress(Exception):
                        tasks = await devloop.plan_tasks(mission, planner, cap)
                    if len(tasks) >= 2:
                        break
        self._plans[mission_id] = tasks  # cached for the build to reuse (never re-decompose)
        if tasks:
            # A ticket per task, each with its real instructions — assignment (agent_name) is filled in
            # at build start (on_build_start) from the actual role→agent match, so tickets == Live Build.
            slices = [{"subtask_id": t.title, "title": t.title, "role": t.role,
                       "instructions": t.instructions, "skills": list(t.skills),
                       "acceptance": list(t.acceptance), "files": list(t.files),
                       "depends_on": list(t.depends_on)} for t in tasks]
        elif not will_parallel:
            # Genuine solo build → ONE "build" story (matches _real_build's solo subtask_id).
            brief = (mission.requirements or mission.summary or mission.title or "").strip()
            slices = [{"subtask_id": "build", "title": mission.title,
                       "role": _enum_value(AgentRoleKey.BACKEND), "instructions": brief,
                       "skills": [], "files": [], "depends_on": []}]
        else:
            # Parallel was wanted but the planner didn't yield a graph this time — create NOTHING here
            # (a placeholder would orphan). The build decomposes and on_build_start creates the real
            # per-dev tickets from the ACTUAL subtasks, so the board still matches the Live Build.
            slices = []
        if slices:
            await self._tickets.plan_stories(
                mission, slices, created_by=await self._ticket_actor(mission, AgentRoleKey.PM))
        msg = (f"Planned {len(slices)} task(s); tickets created in To Do." if slices
               else "Tickets will be created per engineer as the build fans out.")
        await self._emit(run, mission, AgentRoleKey.PM, "spec", msg,
                         payload={"kind": "plan", "tasks": slices})

    async def submit_clarification(self, blocker_id: str, answers: list[dict], *, actor: str | None = None) -> Blocker:
        blocker = await self.store.get_blocker(blocker_id)
        if blocker is None:
            raise KeyError(blocker_id)
        resolved = await self.store.resolve_blocker_record(blocker_id, resolved_by=actor)
        fut = self._pending.pop(blocker_id, None)
        if fut is not None and not fut.done():
            fut.set_result(json.dumps(answers))
        return resolved

    async def _gate_run(self, mission_id: str, workspace_id: str) -> Run | None:
        """The run actually parked at the merge gate — the one an approval should finalize.

        A merge approval belongs to the run that RAISED the gate, which is not necessarily the
        newest run: a fresh re-run may have been started after the gate was raised. Return a run that
        is genuinely AT the gate — BLOCKED, or with a GATED ship step — and nothing else. We do NOT
        fall back to an arbitrary recent run: a still-RUNNING run is not parked at any gate, and
        finalizing it would ship partial work under a stale approval. Cancelled/superseded runs are
        excluded so an approval can never resurrect a dead run. (Blockers carry no run_id, so we
        recover the linkage from run/step state.)
        """
        runs = [r for r in await self.store.list_runs(workspace_id)
                if r.mission_id == mission_id and _enum_value(r.status) != "cancelled"]
        runs.sort(key=lambda r: str(r.started_at or ""))
        blocked = [r for r in runs if _enum_value(r.status) == "blocked"]
        if blocked:
            return blocked[-1]
        for r in reversed(runs):
            steps = await self.store.list_steps(r.id)
            if any(s.phase == "ship" and _enum_value(s.status) == "gated" for s in steps):
                return r
        return None  # no run is actually parked at the gate

    async def _finalize_gate(self, mission_id: str, decision: ApprovalDecision) -> None:
        mission = await self.store.get_mission(mission_id)
        if mission is None:
            return
        run = await self._gate_run(mission_id, mission.workspace_id)
        if run is None:
            # No run is actually parked at the gate — e.g. it was cancelled/superseded in a race
            # between this approval and a stop/new-run. Never ship a mission with no live gated run.
            return
        # The ship step is stuck GATED after a restart-recovery approval — resolve it so the
        # pipeline, team rail, and progress stop showing "needs approval".
        ship_step = next((s for s in await self.store.list_steps(run.id) if s.phase == "ship"), None)

        if decision is ApprovalDecision.REJECT or decision == ApprovalDecision.REJECT.value:
            if run:
                await self.store.update_run(run.id, status=RunStatus.FAILED, finished_at=_now())
            if ship_step:
                await self.store.update_step(ship_step.id, status=StepStatus.BLOCKED)
            await self.store.update_mission(mission_id, stage=MissionStage.REVIEW, is_blocked=False)
            await self._reset_agents_idle(mission.workspace_id)
            if run:
                await self._emit(run, mission, AgentRoleKey.CTO, "review",
                                 "Merge rejected — returned to review", payload={"kind": "gate.rejected"})
            return
        # Push / open the PR FIRST (reopens the on-disk sandbox if the in-process one was lost on
        # restart), and only mark the mission shipped if the push actually succeeded.
        if run:
            pushed_ok = await self._open_pr_if_built(run.id, mission_id)
            if not pushed_ok:
                # The push needs the user's decision (rejected / no token). Re-open the merge gate so
                # they can authorize a force-push or choose a different branch — never resolved for them.
                detail = ("The push was rejected. Authorize a force-push to overwrite that branch, or "
                          "pick a different branch, then approve again.")
                reblock = Blocker(
                    id=new_ulid(), org_id=mission.org_id, workspace_id=mission.workspace_id,
                    mission_id=mission.id, kind=BlockerKind.APPROVAL, severity=BlockerSeverity.WARN,
                    detail=detail, created_at=_now(),
                )
                await self.store.add_blocker(reblock)
                await self._notifier.on_blocker(reblock, mission)
                if ship_step:
                    await self.store.update_step(ship_step.id, status=StepStatus.GATED)
                await self.store.update_run(run.id, status=RunStatus.BLOCKED)
                await self.store.update_mission(mission_id, is_blocked=True)
                await self._emit(run, mission, AgentRoleKey.DEVOPS, "deploy", f"⏸ {detail}",
                                 payload={"kind": "gate.awaiting", "gate": ApprovalGate.MERGE.value,
                                          "blockerId": reblock.id})
                return
            await self.store.update_run(run.id, status=RunStatus.SUCCEEDED, finished_at=_now())
        if ship_step:
            await self.store.update_step(ship_step.id, status=StepStatus.DONE, finished_at=_now())
        shipped = await self.store.update_mission(
            mission_id, stage=MissionStage.SHIPPED, progress=100, is_blocked=False
        )
        await self._tickets.on_shipped(shipped)  # board: close the Epic + Stories FIRST (never gated)
        await self._notifier.on_completed(shipped)  # email/WhatsApp/Slack (failure-isolated)
        await self._bump_agent_stat(AgentRoleKey.DEVOPS, "shipped", shipped.workspace_id, mission=shipped)
        await self._reset_agents_idle(shipped.workspace_id)
        await self._persist_memory(shipped)  # the org remembers what it shipped
        await self._persist_skill(shipped)   # …and codifies a reusable skill it applied
        if run:
            await self._emit(run, shipped, AgentRoleKey.DEVOPS, "deploy",
                             f"✓ {shipped.key} — pipeline complete, merge approved")

    # ---- execution (decision graph) ---------------------------------------------
    async def _execute(self, run_id: str, mission_id: str, *, start_phase: str = "intake") -> None:
        """Drive the mission through the pipeline as a DECISION GRAPH, not a straight line.

        Each phase yields an ``Outcome`` whose verdict routes the work forward, back to an earlier
        phase (build↔QA rework, review→Backend), or into a CTO consultation that can redesign,
        rebuild, or proceed. ``_next_phase`` owns the edges + loop caps; ``reached`` gives a
        monotonic progress bar even while the work loops.
        """
        m0 = await self.store.get_mission(mission_id)
        run_attrs = {
            tracing.ATTR_RUN_ID: run_id,
            tracing.ATTR_MISSION_KEY: m0.key if m0 else None,
            tracing.ATTR_AUTONOMY: _enum_value(m0.autonomy) if m0 else None,
            tracing.ATTR_MISSION_SOURCE: _enum_value(m0.source) if m0 else None,
        }
        # `mission.run` is the root trace for a Run; it outlives the POST that started it.
        with tracing.span("mission.run", run_attrs, root=True) as run_span:
            try:
                # Task ROUTER: classify the work up front so a non-code task (e.g. "push to a branch")
                # skips the heavyweight Backend build and goes to the right role directly.
                task_kind = await self._classify_task(m0) if m0 else "code"
                if task_kind == "ops" and m0 is not None:
                    # A pure git op: DevOps handles it directly, skipping the Backend build + QA.
                    await self._emit_note(run_id, mission_id, AgentRoleKey.CTO,
                                          "🧭 Task router: pure git/deploy task → DevOps handles it directly, "
                                          "skipping the build.", kind="triage")
                    # Push the existing code — target the branch the LATEST request names.
                    target = self._extract_branch(self._latest_directive(m0))
                    if target and target != m0.branch:
                        await self.store.update_mission(mission_id, branch=target)
                        await self._emit_note(run_id, mission_id, AgentRoleKey.DEVOPS,
                                              f"🌿 DevOps will push the current code to branch `{target}`.",
                                              kind="branch")
                elif task_kind == "docs":
                    # A docs/README edit STILL goes through the build (someone edits the file); just say so.
                    await self._emit_note(run_id, mission_id, AgentRoleKey.CTO,
                                          "🧭 Task router: docs change → the build will edit the docs, then "
                                          "QA + review as usual.", kind="triage")
                phase_key: str | None = start_phase if start_phase in _PHASE_BY_KEY else "intake"
                cycles: dict[str, int] = {}   # rework/escalation counters (loop caps)
                visits: dict[str, int] = {}   # per-phase attempt counter (for the agent's context)
                reached = 0                   # furthest canonical index → monotonic progress
                guard = 0
                while phase_key is not None:
                    guard += 1
                    if guard > _PIPELINE_SAFETY_STOP:  # absolute backstop; never expected
                        await self._halt_for_user(
                            run_id, mission_id, AgentRoleKey.CTO,
                            "the pipeline made too many transitions without converging — needs review")
                        tracing.set_attributes(run_span, {tracing.ATTR_RESULT: "blocked"})
                        return
                    # Cooperative cancellation: if this run was superseded/cancelled (a hard task
                    # cancel may not have landed yet, or none was registered), stop before doing more
                    # work — never ship or gate a cancelled run.
                    cur = await self.store.get_run(run_id)
                    if cur is None or _enum_value(cur.status) == "cancelled":
                        return
                    phase = _PHASE_BY_KEY[phase_key]
                    visits[phase_key] = visits.get(phase_key, 0) + 1
                    try:
                        outcome = await self._run_phase(run_id, mission_id, phase, run_span,
                                                        attempt=visits[phase_key])
                    except Exception as exc:  # any role's phase failed after its own recovery
                        # Error-recovery ladder (Canon): the role already tried to self-heal (model
                        # failover); now escalate to the CTO, and only involve the human as a last
                        # resort. Returns a phase to retry, or None when it's been handed to the user.
                        retry = await self._escalate_error(run_id, mission_id, phase, exc, cycles)
                        if retry is None:
                            tracing.record_error(run_span, exc)
                            tracing.set_attributes(run_span, {tracing.ATTR_RESULT: "failed"})
                            return
                        phase_key = retry
                        continue
                    if outcome is None:
                        return  # run/mission vanished (superseded/deleted)
                    if phase_key in CANON_ORDER:
                        reached = max(reached, CANON_ORDER.index(phase_key))
                        await self.store.update_mission(
                            mission_id, progress=min(99, round(reached / (len(CANON_ORDER) - 1) * 100))
                        )
                    if phase_key == "ship":
                        if outcome.token == "APPROVE_GATE":
                            break  # merge approved → ship below
                        tracing.set_attributes(run_span, {tracing.ATTR_RESULT: "blocked"})
                        return  # rejected at the gate — _ship_gate already failed the run
                    next_key = await self._next_phase(
                        run_id, mission_id, phase_key, outcome, cycles, task_kind)
                    # A build only ships via the ship gate's APPROVE_GATE break above. Any other loop
                    # exit — a fail-safe halt, or an unhandled/None transition — STOPS here and never
                    # auto-ships (the halt already blocked the run for the user).
                    if next_key is None or next_key == _HALT:
                        tracing.set_attributes(run_span, {tracing.ATTR_RESULT: "blocked"})
                        return
                    phase_key = next_key

                # Final guard: a run cancelled during the last phase must not ship.
                final = await self.store.get_run(run_id)
                if final is None or _enum_value(final.status) == "cancelled":
                    return
                await self.store.update_run(run_id, status=RunStatus.SUCCEEDED, finished_at=_now())
                await self.store.update_mission(
                    mission_id, stage=MissionStage.SHIPPED, progress=100, is_blocked=False
                )
                tracing.set_attributes(run_span, {tracing.ATTR_RESULT: "shipped"})
                m = await self.store.get_mission(mission_id)
                r = await self.store.get_run(run_id)
                if m and r:
                    # Close the board FIRST — move every Story + the Epic to Done — so a shipped
                    # mission's tickets always land in Done, never gated behind the best-effort stat/
                    # memory bumps below (this autonomous ship path previously skipped it entirely).
                    await self._tickets.on_shipped(m)
                    await self._bump_agent_stat(AgentRoleKey.DEVOPS, "shipped", m.workspace_id, mission=m)
                    await self._reset_agents_idle(m.workspace_id)
                    await self._persist_memory(m)  # the org remembers what it shipped
                    await self._persist_skill(m)   # …and codifies a reusable skill it applied
                    await self._emit(r, m, AgentRoleKey.DEVOPS, "deploy",
                                     f"✓ {m.key} — pipeline complete, merge approved")
            except ProviderError as exc:
                tracing.record_error(run_span, exc)
                tracing.set_attributes(run_span, {tracing.ATTR_RESULT: "failed"})
                await self._fail(run_id, mission_id, f"provider error: {exc}")
            except Exception as exc:  # pragma: no cover - defensive
                tracing.record_error(run_span, exc)
                tracing.set_attributes(run_span, {tracing.ATTR_RESULT: "failed"})
                await self._fail(run_id, mission_id, f"engine error: {exc}")

    async def _run_phase(
        self, run_id: str, mission_id: str, phase: Phase, run_span: tracing.Span, *, attempt: int = 1
    ) -> Outcome | None:
        """Execute one pipeline phase inside its own ``step.<phase>`` span, returning its verdict.

        Returns None only when the run/mission has vanished (superseded or deleted mid-run).
        """
        mission = await self.store.get_mission(mission_id)
        run = await self.store.get_run(run_id)
        if mission is None or run is None:
            return None
        await self.store.update_mission(mission.id, stage=phase.stage)

        step_attrs = {tracing.ATTR_PHASE: phase.key, tracing.ATTR_AGENT_ROLE: _enum_value(phase.role)}
        with tracing.span(f"step.{phase.key}", step_attrs):
            step = await self._begin_step(run, phase)
            if phase.key == "ship":
                proceeded = await self._ship_gate(run_id, mission_id, step.id)
                return Outcome("APPROVE_GATE") if proceeded else Outcome("REJECT_GATE")
            if phase.key == "clarify":
                await self._clarify_gate(run_id, mission_id, step.id)
                await self._finish_step(step.id, run_id, mission_id, phase)
                return Outcome("ADVANCE")
            if phase.key == "plan":
                await self._plan_gate(run_id, mission_id, step.id)
                await self._finish_step(step.id, run_id, mission_id, phase)
                return Outcome("ADVANCE")
            text = ""
            if phase.key == "build.api" and self.sandbox_enabled:
                await self._real_build(run_id, mission_id, phase, step.id)
            elif phase.purpose:
                text = await self._run_llm_phase(run_id, mission_id, phase, step.id, attempt=attempt) or ""
            outcome = await self._decide(run_id, mission_id, phase, text)
            # Record the verdict on the step so resume/retry can tell a PASSED checkpoint from a
            # REWORK one (a step is DONE either way) and never skip re-verification of unpassed work.
            await self._finish_step(step.id, run_id, mission_id, phase,
                                    detail=f"verdict:{outcome.token}" if outcome.token != "ADVANCE" else None)
            return outcome

    async def _decide(self, run_id: str, mission_id: str, phase: Phase, text: str) -> Outcome:
        """Turn a decision phase's reply into a routing Outcome — robustly.

        1. Parse the verdict. Non-decision phases just ADVANCE.
        2. If NO verdict line was emitted (e.g. the analysis was long/truncated), re-elicit it with a
           tiny focused follow-up call, so a missing verdict is recovered instead of read as PASS.
        3. GROUND-TRUTH override: QA cannot PASS a build that objectively failed (no real work / stub /
           failing tests) — a hallucinated PASS is overridden to REWORK. This is the gate a model can't
           talk its way past.
        """
        outcome = _parse_verdict(phase.key, text)
        if phase.key not in _VERDICT_VALID:
            return outcome
        if outcome.token == _NO_VERDICT:
            outcome = await self._elicit_verdict(run_id, mission_id, phase, text)
        if outcome.token == _NO_VERDICT:  # still nothing after re-elicitation → fail safe (hold)
            outcome = Outcome(_VERDICT_FALLBACK[phase.key], "no verdict emitted — holding for safety")
        if phase.key == "qa" and outcome.token == "PASS":
            healthy, why = self._build_is_healthy(mission_id)
            if not healthy:
                await self._emit_note(run_id, mission_id, AgentRoleKey.QA,
                                      f"⛔ QA verdict overridden to REWORK — the build did not actually "
                                      f"pass ground-truth checks: {why}", kind="groundtruth")
                outcome = Outcome("REWORK", f"ground-truth: {why}")
        if phase.key == "qa":
            # Reduce (LLM token + ground-truth evidence) → PASS | REWORK | PARTIAL. The reducer is
            # AUTHORITATIVE (the model never has the last word) so a mislabelled PARTIAL can't ship a
            # broken build. Board: PASS → In Review; PARTIAL → In Review + tracked follow-up bug;
            # REWORK → reopen the failing Story + blocking Bug.
            healthy, _why = self._build_is_healthy(mission_id)
            evidence = (self._build_facts.get(mission_id, {}) or {}).get("qa_evidence") or {}
            disp = _classify_qa(evidence, outcome.token, outcome.reason, healthy)
            outcome = Outcome(disp, outcome.reason)
            mission = await self.store.get_mission(mission_id)
            if mission is not None:
                actor = await self._ticket_actor(mission, AgentRoleKey.QA)
                if disp == "PARTIAL":
                    failing = [c.get("criterion_id") for c in (evidence.get("checks") or [])
                               if c.get("criterion_id") and c.get("status") == "fail"]
                    await self._tickets.on_qa_partial(
                        mission, run_id, reason=outcome.reason, failing_criteria=failing,
                        evidence=evidence, qa_actor=actor)
                else:
                    if disp == "REWORK":
                        # Localize the failure to the owning subtask(s) so the next build rebuilds ONLY
                        # those parts (off the current code), not the whole app. Empty ⇒ rebuild all.
                        targets = self._failing_subtasks(mission_id, evidence, outcome.reason)
                        if targets:
                            self._rework_targets[mission_id] = targets
                        else:
                            self._rework_targets.pop(mission_id, None)
                    await self._tickets.on_qa(
                        mission, run_id, passed=(disp == "PASS"), reason=outcome.reason,
                        evidence=evidence, actor=actor)
        return outcome

    def _failing_subtasks(self, mission_id: str, evidence: dict, reason: str) -> set[str]:
        """Best-effort map QA's failing criteria → the subtask TITLES that own them, so a rework
        rebuilds only those parts. Matches on the files a subtask owns and its title tokens; falls
        back to the role (backend/frontend) when the failure clearly names a layer but no file/title.
        Returns an empty set when it can't localize — the caller then rebuilds everything (safe)."""
        subs = self._plans.get(mission_id) or []
        if not subs:
            return set()
        fails = [reason or ""]
        for c in evidence.get("checks") or []:
            if c.get("status") == "fail":
                fails += [str(c.get("criterion") or ""), str(c.get("name") or ""),
                          str(c.get("criterion_id") or "")]
        blob = " ".join(fails).lower()
        if not blob.strip():
            return set()
        hit: set[str] = set()
        for st in subs:
            files = [str(f).rsplit("/", 1)[-1].lower() for f in (getattr(st, "files", []) or [])]
            toks = [w for w in re.findall(r"[a-z0-9]+", (st.title or "").lower()) if len(w) > 3]
            if any(f and f in blob for f in files) or any(w in blob for w in toks):
                hit.add(st.title)
        if not hit:  # role-level fallback when the text names a layer but not a specific file/title
            be = any(k in blob for k in ("/api/", "endpoint", "server.js", "backend", "route", "http server"))
            fe = any(k in blob for k in ("frontend", " ui", " page", "css", "render", "html", "styl", "app.js"))
            for st in subs:
                role = _enum_value(getattr(st, "role", ""))
                if (be and role == "backend") or (fe and role == "frontend"):
                    hit.add(st.title)
        return hit

    async def _elicit_verdict(self, run_id: str, mission_id: str, phase: Phase, analysis: str) -> Outcome:
        """Focused follow-up call whose ONLY job is to emit the VERDICT line, given the analysis the
        phase already produced. Guarantees a routing token even when the main reply was truncated
        before it. Bounded output → cannot itself be truncated before the verdict."""
        mission = await self.store.get_mission(mission_id)
        run = await self.store.get_run(run_id)
        if mission is None or run is None:
            return Outcome(_NO_VERDICT)
        ask = _VERDICT_ASK.get(phase.key, "")
        tail = (analysis or "").strip()[-2500:]
        prompt = (
            f"Here is your analysis for this ticket:\n\n{tail}\n\n"
            f"Based ONLY on that analysis, output the decision now.\n{ask}\n"
            "Reply with just the single VERDICT line — nothing else."
        )
        try:
            providers = await self._resolve_providers(mission, phase.role)
            result = await self._complete_failover(
                providers, run, mission, phase.role,
                system="You output exactly one line: the VERDICT. No preamble.",
                prompt=prompt, purpose="verdict", max_tokens=40,
            )
        except ProviderError:
            return Outcome(_NO_VERDICT)
        return _parse_verdict(phase.key, result.text or "")

    async def _next_phase(
        self, run_id: str, mission_id: str, current: str, outcome: Outcome, cycles: dict[str, int],
        task_kind: str = "code",
    ) -> str | None:
        """Resolve the next phase from a verdict — the edges of the decision graph. Backward edges
        (rework/escalate/redesign) are what make the pipeline non-linear; ``cycles`` caps them so a
        loop is always finite. Emits a short, legible event on every non-forward move.

        ``task_kind`` (from the task router) lets a PURE git op ("ops") skip the Backend build + QA
        and go straight to review → ship, where DevOps performs the git/deploy action directly. A
        "docs" change still runs the build (someone has to edit the file), same as "code"."""
        v = outcome.token
        reason = outcome.reason

        async def note(role: AgentRoleKey, text: str, kind: str) -> None:
            await self._emit_note(run_id, mission_id, role, text, kind=kind)

        if current == "intake":
            return "clarify"
        if current == "clarify":
            return "spec"
        if current == "spec":
            # Only a PURE git op skips build+plan; code AND docs go through planning then the build.
            return "review" if task_kind == "ops" else "plan"
        if current == "plan":
            return "build.api"  # tickets are planned → engineers build
        if current == "build.api":
            return "review"  # code review first, then QA verifies the reviewed build
        # Each backward edge has its OWN capped budget so one edge can't starve another (QA reworks
        # must not use up the review→Backend budget). A CTO REDESIGN/REBUILD is a deliberate new
        # direction, so it RESETS the rework budgets to let the fresh approach iterate.
        if current == "qa":
            # QA is the FINAL gate (the code was already reviewed) → a pass ships.
            if v == "PASS":
                cycles["qa_passed"] = 1  # QA has signed off (gates any CTO proceed)
                return "ship"
            if v == "PARTIAL":
                # Core criteria pass; a minor portion is deferred to a tracked follow-up bug. Ship with
                # the follow-up riding along as backlog (do NOT set qa_passed — not a full sign-off; a
                # blocking gap can't be here, the reducer guarantees it).
                cycles["qa_partial"] = 1
                await note(AgentRoleKey.QA,
                           "➡️ QA partial: core criteria pass; shipping with a tracked follow-up bug.",
                           "partial")
                return "ship"
            # No-progress guard: if we've ALREADY reworked and the latest rebuild changed nothing
            # (0 net writes despite doing work) yet QA still fails, another identical rebuild won't
            # help — stop repeating the same tasks in a loop and escalate straight to the CTO (or the
            # human if the CTO budget is spent). This is the safety net for any future grounding/build
            # bug that could otherwise burn the whole rework budget on a stuck build.
            facts = self._build_facts.get(mission_id) or {}
            stuck = (cycles.get("qa_rework", 0) > 0
                     and facts.get("files_written") == 0 and int(facts.get("steps", 0) or 0) > 0)
            if stuck:
                if cycles.get("cto_decisions", 0) < MAX_CTO_DECISIONS:
                    await note(AgentRoleKey.QA,
                               "QA still fails and the last rework changed nothing — escalating to the "
                               "CTO instead of repeating the same build.", "rework.stuck")
                    return "cto.decision"
                return await self._halt_for_user(
                    run_id, mission_id, AgentRoleKey.QA,
                    "QA is not passing and reworks have stopped changing the build"
                    + (f": {reason}" if reason else "") + ". Needs your input — review the acceptance "
                    "criteria and Retry, or adjust the ticket.")
            # QA is not satisfied. Rework while budget remains; then escalate to the CTO for a
            # decision (redesign/rebuild) — NEVER force a QA-failing build forward to ship.
            if cycles.get("qa_rework", 0) < MAX_REWORK_CYCLES:
                cycles["qa_rework"] = cycles.get("qa_rework", 0) + 1
                self._rework_reason[mission_id] = reason or ""  # QA's fix instructions → the builders
                await note(AgentRoleKey.QA,
                           f"🔁 QA found gaps → back to Backend "
                           f"(rework {cycles['qa_rework']}/{MAX_REWORK_CYCLES})"
                           + (f": {reason}" if reason else ""), "rework")
                return "build.api"
            if cycles.get("cto_decisions", 0) < MAX_CTO_DECISIONS:
                await note(AgentRoleKey.QA,
                           f"QA still fails after {MAX_REWORK_CYCLES} reworks — escalating to the CTO "
                           "for a direction.", "rework.capped")
                return "cto.decision"
            return await self._halt_for_user(
                run_id, mission_id, AgentRoleKey.QA,
                f"QA could not pass after {MAX_REWORK_CYCLES} reworks and a CTO consultation"
                + (f": {reason}" if reason else "") + ". The build does not meet the acceptance "
                "criteria — review the requirements and Retry, or adjust the ticket.")
        if current == "review":
            if v == "APPROVE":
                approved_mission = await self.store.get_mission(mission_id)
                if approved_mission is not None:  # board: record WHO reviewed + approved each Story
                    await self._tickets.on_reviewed(
                        approved_mission, approved=True, reason=reason or "",
                        actor=await self._ticket_actor(approved_mission, AgentRoleKey.CTO))
                # Code approved → QA verifies behaviour as the final gate (a pure git op has no build to QA).
                if task_kind == "ops":
                    return "ship"
                await note(AgentRoleKey.CTO,
                           "✅ Code review approved → QA for final acceptance verification.", "review.approved")
                return "qa"
            if v == "REWORK":
                if cycles.get("review_rework", 0) < MAX_REWORK_CYCLES:
                    cycles["review_rework"] = cycles.get("review_rework", 0) + 1
                    review_mission = await self.store.get_mission(mission_id)
                    if review_mission is not None:  # board: Stories back to In Progress (isolated)
                        await self._tickets.on_review_changes(
                            review_mission, reason or "Changes requested",
                            actor=await self._ticket_actor(review_mission, AgentRoleKey.CTO))
                    self._rework_reason[mission_id] = reason or ""  # review's change request → builders
                    # Target the rework at the part(s) the review actually flagged (e.g. a frontend
                    # style note goes to the frontend dev only) — don't rebuild backend for a UI change.
                    r_targets = self._failing_subtasks(mission_id, {}, reason)
                    if r_targets:
                        self._rework_targets[mission_id] = r_targets
                    await note(AgentRoleKey.CTO,
                               f"🔁 Review: changes needed → back to Backend "
                               f"(rework {cycles['review_rework']}/{MAX_REWORK_CYCLES})"
                               + (f": {reason}" if reason else ""), "rework")
                    return "build.api"
                if cycles.get("cto_decisions", 0) < MAX_CTO_DECISIONS:
                    return "cto.decision"
                return await self._halt_for_user(
                    run_id, mission_id, AgentRoleKey.CTO,
                    "Review still requires changes after the rework budget was exhausted"
                    + (f": {reason}" if reason else "") + ". Needs your decision.")
            # ESCALATE (or any held verdict) → CTO decision while the consultation budget remains.
            if cycles.get("escalations", 0) < MAX_ESCALATIONS and cycles.get("cto_decisions", 0) < MAX_CTO_DECISIONS:
                cycles["escalations"] = cycles.get("escalations", 0) + 1
                await note(AgentRoleKey.CTO,
                           f"⬆️ Review escalated to the CTO for a decision "
                           f"({cycles['escalations']}/{MAX_ESCALATIONS})"
                           + (f": {reason}" if reason else ""), "escalate")
                return "cto.decision"
            return await self._halt_for_user(
                run_id, mission_id, AgentRoleKey.CTO,
                "Review could not be resolved automatically" + (f": {reason}" if reason else "")
                + ". Needs your decision.")
        if current == "cto.decision":
            cycles["cto_decisions"] = cycles.get("cto_decisions", 0) + 1
            if v == "REDESIGN":
                cycles["qa_rework"] = 0  # a new approach earns fresh rework budgets
                cycles["review_rework"] = 0
                self._rework_targets.pop(mission_id, None)  # a redesign rebuilds the WHOLE app, not a slice
                await note(AgentRoleKey.CTO,
                           "↩️ CTO decision: redesign → back to Spec" + (f": {reason}" if reason else ""),
                           "redesign")
                return "spec"
            if v == "REBUILD":
                cycles["qa_rework"] = 0
                cycles["review_rework"] = 0
                self._rework_targets.pop(mission_id, None)  # a rebuild is a fresh full build, not a slice
                self._rework_reason[mission_id] = reason or ""  # the CTO's direction → the builders
                await note(AgentRoleKey.CTO,
                           "↩️ CTO decision: rebuild → back to Backend" + (f": {reason}" if reason else ""),
                           "rebuild")
                return "build.api"
            # PROCEED: the CTO is the senior authority — but a proceed can ship ONLY when quality is
            # real: the build passed ground-truth AND QA has signed off. An unhealthy build never ships.
            healthy, why = self._build_is_healthy(mission_id)
            if not healthy:
                return await self._halt_for_user(
                    run_id, mission_id, AgentRoleKey.CTO,
                    f"The build did not pass ground-truth checks ({why}); it cannot be shipped as-is. "
                    "Needs your decision.")
            if cycles.get("qa_passed"):
                await note(AgentRoleKey.CTO,
                           "▶️ CTO decision: proceed to merge" + (f": {reason}" if reason else ""), "proceed")
                return "ship"
            # QA has NOT passed. In review→QA order the CTO is often consulted from a REVIEW escalation
            # (before QA runs) — send it to the QA gate the CTO is trusting. But if QA was already
            # attempted and could not pass, never ship it silently — hand to the human.
            if cycles.get("qa_rework", 0) > 0 or cycles.get("qa_partial"):
                return await self._halt_for_user(
                    run_id, mission_id, AgentRoleKey.CTO,
                    "the CTO chose to proceed, but QA could not pass this build — shipping it needs your "
                    "explicit sign-off. Review the requirements and Retry, or adjust the ticket.")
            await note(AgentRoleKey.CTO,
                       "▶️ CTO decision: proceed → QA for final verification"
                       + (f": {reason}" if reason else ""), "proceed")
            return "qa"
        return None

    async def _halt_for_user(
        self, run_id: str, mission_id: str, role: AgentRoleKey, message: str
    ) -> str:
        """Fail-safe STOP: the automated team could not produce a shippable build, so hand it to the
        human instead of auto-advancing unverified/failing work to ship. This is a needs-human FAILURE
        (retryable), NOT a merge approval — so it must not raise an ``approval`` blocker (that would pop
        the push/merge gate over a build with nothing real to push). It emits a clear ask, fails the
        run (Retry resumes after the user adjusts models/requirements), and parks the mission as STOPPED
        so the board/dashboard don't count it as still building. Returns ``_HALT`` so ``_execute`` stops
        WITHOUT shipping."""
        run = await self.store.get_run(run_id)
        mission = await self.store.get_mission(mission_id)
        if run is None or mission is None:
            return _HALT
        await self._emit(run, mission, role, "review",
                         f"🙋 Needs your attention: {message}",
                         payload={"kind": "needs.user", "phase": "review"})
        await self._fail(run_id, mission_id, message)
        await self._notifier.on_failed(mission, message)  # email/WhatsApp/Slack (failure-isolated)
        if (m := await self.store.get_mission(mission_id)) is not None and m.stage is not MissionStage.SHIPPED:
            await self.store.update_mission(mission_id, stage=MissionStage.STOPPED, is_blocked=False)
        return _HALT

    # ---- error-recovery ladder: role self-heals → CTO → user (Canon) ------------
    async def _escalate_error(
        self, run_id: str, mission_id: str, phase: Phase, error: Exception, cycles: dict[str, int]
    ) -> str | None:
        """A role's phase failed after its own recovery (model failover). Escalate up the ladder:
        the role reports the error, the CTO is consulted to decide whether a retry can fix it, and
        only if the CTO can't is the human involved (the run fails with a clear ask; Retry resumes).
        Returns the phase to retry, or None when it's been handed to the user."""
        run = await self.store.get_run(run_id)
        mission = await self.store.get_mission(mission_id)
        if run is None or mission is None:
            return None
        msg = str(error)[:280]
        # 1) The role reads + reports its own error.
        await self._emit(run, mission, phase.role, "error",
                         f"❌ {_enum_value(phase.role)} hit an error in {phase.key}: {msg}",
                         payload={"kind": "phase.error", "phase": phase.key})

        if cycles.get("error_escalations", 0) >= MAX_ERROR_ESCALATIONS:
            await self._fail_for_user(run_id, mission_id, phase, msg,
                                      "the error persisted after the CTO's retries")
            return None
        cycles["error_escalations"] = cycles.get("error_escalations", 0) + 1

        # 2) Escalate to the CTO, who decides whether a retry is worth it.
        await self._emit(run, mission, AgentRoleKey.CTO, "review",
                         f"⬆️ {_enum_value(phase.role)} couldn't resolve it — escalating to the CTO",
                         payload={"kind": "route.escalate", "phase": phase.key})
        decision = await self._cto_error_decision(mission, phase, msg)
        if decision == "RETRY":
            await self._emit(run, mission, AgentRoleKey.CTO, "review",
                             f"↩️ CTO: retrying {phase.key} — the error looks recoverable",
                             payload={"kind": "route.rebuild", "phase": phase.key})
            return phase.key
        # 3) Last resort: involve the human.
        await self._fail_for_user(run_id, mission_id, phase, msg, "the CTO judged it needs a human")
        return None

    async def _cto_error_decision(self, mission: Mission, phase: Phase, error_msg: str) -> str:
        """Ask the CTO whether a failed phase should be RETRIED or handed to a HUMAN. Errors that
        are configuration/credential/limit/access issues need a human; transient ones can retry.
        Defaults to HUMAN when unsure (never loops on an unfixable error)."""
        try:
            provider = (await self._resolve_providers(mission, AgentRoleKey.CTO))[0]
            result = await provider.complete(
                system="You are the CTO triaging a failed pipeline step. Be decisive.",
                prompt=(
                    f"Phase '{phase.key}' failed with:\n{error_msg}\n\n"
                    "Decide: can a plain RETRY plausibly fix this (transient/network/rate blip), or "
                    "does it need a HUMAN (bad/missing credentials, access denied, quota/limit, "
                    "repo/config problem, or anything a retry won't change)?\n"
                    "Reply with EXACTLY one word: RETRY or HUMAN."
                ),
                purpose="triage", max_tokens=8,
            )
            return "RETRY" if "RETRY" in (result.text or "").upper() else "HUMAN"
        except Exception:  # pragma: no cover - if even triage fails, involve the human
            return "HUMAN"

    @staticmethod
    def _latest_directive(mission: Mission) -> str:
        """The user's MOST RECENT instruction — the last ``Change request:`` block when a shipped
        mission has been reopened, else the full brief. Routing/branch-extraction must use THIS, not
        the accumulated history: an old 'just push, no changes' must never shadow a new
        'modify the readme and push'."""
        text = (mission.requirements or mission.summary or mission.title or "")
        marker = "Change request:"
        if marker in text:
            return text.rsplit(marker, 1)[1].strip()
        return text.strip()

    async def _classify_task(self, mission: Mission) -> str:
        """The task ROUTER: decide what kind of work this needs so it goes to the right role. Returns:
          * "code" — application code must be written/changed → full build (Backend) → QA → …
          * "docs" — a docs/README file must be edited → still goes through the build (someone edits
                     the file), just flagged so the console reads clearly.
          * "ops"  — a PURE git/deploy action with NO file change (push existing code to a branch,
                     tag, merge, deploy) → DevOps handles it directly, skipping the build + QA.

        Classify on the LATEST instruction only (``_latest_directive``), never the accumulated
        history: an old "just push, no changes" must not shadow a new "modify the readme". A request
        that edits file content is code/docs even if it also says "push" — the push is just the ship.
        """
        brief = self._latest_directive(mission)
        blob = brief.lower()
        # Does the request ask to CHANGE file content? Such verbs mean a build is needed (to edit +
        # commit the file), even when the request also says "push to main".
        change_verb = any(k in blob for k in
                          ("modify", "update", "edit", "add ", "added", "create ", "change ", "write ",
                           "implement", "fix ", "refactor", "improve", "rewrite", "remove ", "delete "))
        docs_target = any(k in blob for k in
                          ("readme", "documentation", "changelog", "license", "contributing", " docs"))
        git_only = any(k in blob for k in
                       ("new branch", "branch named", "create a branch", "another branch", "merge into",
                        "rebase", "cherry-pick", "tag ", "deploy", "release"))
        no_change = any(k in blob for k in
                        ("same code", "existing code", "existing project", "no change", "no changes",
                         "without change", "as-is", "as is", "don't change", "dont change",
                         "just push", "only push", "push what"))
        # Editing a docs file → docs (build edits it). "modify the readme and push" lands here, NOT ops.
        if docs_target and change_verb:
            return "docs"
        # A PURE git op: a git/deploy action, no file-content change requested. Requires either an
        # explicit "no change" cue or a git-only verb with no content-change verb present.
        if not change_verb and (no_change or git_only or ("push" in blob and "branch" in blob)):
            return "ops"
        try:
            provider = (await self._resolve_providers(mission, AgentRoleKey.CTO))[0]
            result = await provider.complete(
                system="You route software tasks to the right team. Answer with one word.",
                prompt=(
                    f"Latest instruction:\n{brief}\n\n"
                    "Which kind of work does this need?\n"
                    "code = write or change application code\n"
                    "docs = edit a docs/README file\n"
                    "ops  = a git/deploy action with NO file change (e.g. push existing code to a "
                    "branch, create a tag, merge, deploy)\n"
                    "If it edits any file AND also pushes, answer code or docs — pushing happens "
                    "automatically afterwards. Answer ops ONLY for a pure git/deploy action.\n"
                    "Reply with EXACTLY one word: code, docs, or ops."
                ),
                purpose="triage", max_tokens=6,
            )
            word = (result.text or "").strip().lower()
            for kind in ("docs", "ops", "code"):
                if kind in word:
                    return kind
        except Exception:  # pragma: no cover - fall back to the heuristic
            pass
        if docs_target:
            return "docs"
        return "code"

    @staticmethod
    def _extract_branch(text: str) -> str | None:
        """Best-effort pull of a target branch name out of a free-text request, e.g.
        'push the same code to a new branch named feature/todo-app' → 'feature/todo-app'.
        Returns None when no branch is named, so the caller keeps the existing branch."""
        import re
        t = text or ""
        patterns = [
            r"branch\s+named\s+[`'\"]?([A-Za-z0-9._\-/]+)",
            r"branch\s+called\s+[`'\"]?([A-Za-z0-9._\-/]+)",
            r"(?:to|onto|into|on)\s+(?:the\s+)?[`'\"]?([A-Za-z0-9._\-/]+)[`'\"]?\s+branch",  # "to the main branch"
            r"(?:new|a)\s+branch\s+[`'\"]?([A-Za-z0-9._\-/]+)",
            r"push(?:\s+it|\s+the\s+code)?\s+(?:in|into|to)\s+(?:the\s+)?[`'\"]?([A-Za-z0-9._\-/]+)",
            r"branch\s+[`'\"]?([A-Za-z0-9._\-/]+)",
        ]
        stop = {"named", "called", "new", "a", "the", "code", "same", "branch", "to", "into", "in",
                "existing", "project", "repo", "repository", "remote", "it"}
        for pat in patterns:
            m = re.search(pat, t, re.IGNORECASE)
            if m:
                name = m.group(1).strip().strip("`'\".,")
                if name and name.lower() not in stop:
                    return name
        return None

    async def _fail_for_user(
        self, run_id: str, mission_id: str, phase: Phase, error_msg: str, why: str
    ) -> None:
        """Involve the human: fail the run with a clear, actionable ask. The Retry control resumes
        from the checkpoint once the user has fixed the cause (model, repo, credentials, …)."""
        run = await self.store.get_run(run_id)
        mission = await self.store.get_mission(mission_id)
        if run and mission:
            await self._emit(
                run, mission, AgentRoleKey.CTO, "error",
                f"🙋 Needs your input on {phase.key}: {error_msg} — {why}. "
                "Fix the cause (e.g. model, repo, or credentials) and click Retry to resume.",
                payload={"kind": "needs.user", "phase": phase.key},
            )
        await self._fail(run_id, mission_id, f"{phase.key} needs human input: {error_msg}")

    async def _active_provider(self):
        """Resolve the provider for this run from the primary connected model (the UI's active
        model picker); fall back to the injected/env provider when none is configured."""
        try:
            conns = await self.store.list_model_connections()
        except Exception:  # pragma: no cover - defensive
            return self.provider
        primary = next(
            (c for c in conns if c.is_primary and c.status == ConnectionStatus.CONNECTED), None
        )
        if primary is None:
            return self.provider
        cfg = primary.config or {}
        model = cfg.get("activeModel") or (primary.models[0] if primary.models else "")
        from .providers import build_provider

        return build_provider(_enum_value(primary.provider), model, primary.endpoint,
                              cfg.get("apiKey"), config=cfg)

    async def _ticket_actor(self, mission: Mission | None, role: AgentRoleKey) -> tuple[str, str]:
        """(name, role) of the agent that plays ``role`` on this mission — for ticket activity so the
        board shows the real QA/reviewer NAME (e.g. 'Ansa', 'Kamran'), not a generic role label."""
        rv = _enum_value(role)
        with contextlib.suppress(Exception):
            agent = await self._team_agent(mission, role)
            if getattr(agent, "name", None):
                return (agent.name, rv)
        return (rv.upper(), rv)

    # ---- agent liveness + team routing (real status/stats, driven by run execution) -----
    async def _team_agent(self, mission: Mission | None, role: AgentRoleKey):
        """The agent that plays ``role`` for this mission: its Team's **accountable** member for the
        role first, else any team member with the role, else the org roster agent for the role."""
        target = _enum_value(role)
        agents = {a.id: a for a in await self.store.list_agents(mission.workspace_id if mission else "")}
        if mission is not None and mission.team_id:
            team = await self.store.get_team(mission.team_id)
            if team is not None:
                acc = {m.get("agentId") for m in team.members if m.get("accountable")}
                matches = [agents[m["agentId"]] for m in team.members
                           if m.get("agentId") in agents and _enum_value(agents[m["agentId"]].role_key) == target]
                matches.sort(key=lambda a: a.id not in acc)  # accountable member first
                if matches:
                    return matches[0]
        return next((a for a in agents.values() if _enum_value(a.role_key) == target), None)

    async def _set_agent_status(
        self, role: AgentRoleKey, status: AgentStatus, workspace_id: str, mission: Mission | None = None
    ) -> None:
        agent = await self._team_agent(mission, role)
        if agent is not None:
            await self.store.update_agent(agent.id, status=status)

    async def _bump_agent_stat(
        self, role: AgentRoleKey, key: str, workspace_id: str, n: int = 1, mission: Mission | None = None
    ) -> None:
        agent = await self._team_agent(mission, role)
        if agent is not None:
            stats = dict(agent.stats)
            stats[key] = stats.get(key, 0) + n
            await self.store.update_agent(agent.id, stats=stats)

    @staticmethod
    def _agent_model_order(agent) -> list[str]:
        """The models to try for an agent, in order — its SELECTED model (``model_binding``) FIRST,
        then the rest of its failover list. Honouring the binding is what makes "engineers on the local
        coder model, everyone else on Groq" actually take effect at generation time (the binding maps to
        whichever provider connection owns that model, so a local model routes to Ollama, a Groq model
        to Groq)."""
        binding = getattr(agent, "model_binding", None)
        rest = list(agent.models or [])
        ordered = ([binding] if binding else []) + [m for m in rest if m != binding]
        return [m for m in ordered if m]

    @staticmethod
    def _provider_chain(agent, conns: list) -> list:
        """The ordered provider failover chain for an agent — STRICTLY the models the user configured
        and selected on THIS agent: its selected model first, then the rest of its own failover list
        (resolved against connected connections). Failover never borrows a model the agent wasn't given;
        if every configured model is down/out of credits, the call errors (that's the policy)."""
        from .providers import build_provider

        live = [c for c in conns if c.status == ConnectionStatus.CONNECTED]
        chain: list = []
        for model in (RunEngine._agent_model_order(agent) if agent is not None else []):
            conn = next((c for c in live if model in (c.models or [])), None)
            if conn is not None:
                chain.append(build_provider(
                    _enum_value(conn.provider), model, conn.endpoint,
                    (conn.config or {}).get("apiKey"), config=conn.config,
                ))
        return chain

    async def _resolve_providers(self, mission: Mission | None, role: AgentRoleKey) -> list:
        """Ordered providers to try for a phase: the role agent's SELECTED model first, then its own
        failover list, then EVERY other connected model — so a phase never dies on one provider being
        down/out of credits while others are connected. Falls back to the active/primary model."""
        agent = await self._team_agent(mission, role)
        conns = await self.store.list_model_connections(mission.workspace_id if mission else "")
        providers = self._provider_chain(agent, conns)
        if not providers:
            providers.append(await self._active_provider())
        return providers

    @staticmethod
    def _is_solo(mission: Mission) -> bool:
        """Force a single-agent build via a ``solo``/``sequential``/``no-parallel`` label (or that
        phrase in the brief). Greenfield apps are solo by DEFAULT now (see ``_wants_parallel``); this
        still lets a user force solo where parallel would otherwise be opted in."""
        if any(str(lbl).strip().lower() in {"solo", "sequential", "no-parallel", "single-agent"}
               for lbl in (mission.labels or [])):
            return True
        blob = (mission.requirements or mission.summary or "").lower()
        return any(p in blob for p in ("no parallel", "single agent", "one agent", "sequentially by one"))

    @staticmethod
    def _wants_parallel(mission: Mission) -> bool:
        """Opt IN to the fork-join parallel build (a ``parallel`` label, or the phrase in the brief).
        OFF by default because parallel workers build in isolated worktrees — great for splitting work
        across an EXISTING codebase, but it fragments a from-scratch app into placeholder shells."""
        if any(str(lbl).strip().lower() in {"parallel", "fan-out", "multi-agent"}
               for lbl in (mission.labels or [])):
            return True
        blob = (mission.requirements or mission.summary or "").lower()
        return any(p in blob for p in ("in parallel", "parallel build", "fan out", "multi-agent"))

    async def _team_agents_for(self, mission: Mission | None, role: AgentRoleKey) -> list:
        """DISTINCT agents that play ``role`` for this mission — the mission's team members with the
        role if it has a team, else the whole roster — most-senior first. This is the pool the
        parallel fan-out draws from (one subtask per agent)."""
        target = _enum_value(role)
        by_id = {a.id: a for a in await self.store.list_agents(mission.workspace_id if mission else "")}
        agents: list = []
        if mission is not None and mission.team_id:
            team = await self.store.get_team(mission.team_id)
            if team is not None:
                agents = [by_id[m["agentId"]] for m in team.members
                          if m.get("agentId") in by_id and _enum_value(by_id[m["agentId"]].role_key) == target]
        if not agents:
            agents = [a for a in by_id.values() if _enum_value(a.role_key) == target]
        _rank = {"strategic": 0, "principal": 1, "senior": 2, "junior": 3}
        agents.sort(key=lambda a: (_rank.get(_enum_value(a.level), 9), a.name))
        return agents

    async def _resolve_agent_providers(self, mission: Mission | None, role: AgentRoleKey) -> list:
        """ONE provider per DISTINCT agent that plays ``role`` — for the parallel fan-out (each agent
        works independently). Each agent gets a FAILOVER provider over its FULL chain (selected model →
        own failover list → every other connected model), so a build worker whose model is down/out of
        credits transparently retries the next model and only errors when ALL are exhausted. Ordered by
        seniority so the most senior agents take the first subtasks."""
        from .providers import FailoverProvider

        agents = await self._team_agents_for(mission, role)
        conns = await self.store.list_model_connections(mission.workspace_id if mission else "")
        out: list = []
        for agent in agents:
            chain = self._provider_chain(agent, conns)
            out.append(FailoverProvider(chain) if chain else await self._active_provider())
        return out

    async def _complete_failover(self, providers: list, run, mission, role, **kwargs):
        """Call ``complete`` across the ordered providers, failing over to the next on a
        ProviderError so one model being down doesn't sink the run."""
        last: Exception | None = None
        for i, provider in enumerate(providers):
            try:
                return await provider.complete(**kwargs)
            except ProviderError as exc:
                last = exc
                if i + 1 < len(providers):
                    await self._emit(
                        run, mission, role, "error",
                        f"{getattr(provider, 'model', 'model')} failed ({exc}); failing over to the next model",
                        payload={"kind": "model.failover"},
                    )
        raise last or ProviderError("no model available")

    async def _reset_agents_idle(self, workspace_id: str) -> None:
        """Return every non-idle agent to idle — called when a run ends so no agent is left
        showing 'working' after its run finished, failed, or was rejected at the gate."""
        for agent in await self.store.list_agents(workspace_id):
            if _enum_value(agent.status) != _enum_value(AgentStatus.IDLE):
                await self.store.update_agent(agent.id, status=AgentStatus.IDLE)

    async def _recall_memory(self, mission: Mission) -> str:
        """Retrieve the workspace memories most relevant to this mission and format them for the
        prompt, so the team applies past decisions instead of relearning them (Phase 9)."""
        query = f"{mission.title} {mission.summary or ''}"
        memories = await self.store.search_memories(query, k=3)
        if not memories:
            return ""
        lines = "\n".join(f"- {m.title}: {m.body}" for m in memories)
        return f"Relevant team memory (apply it):\n{lines}\n\n"

    async def _persist_memory(self, mission: Mission) -> None:
        """Autonomously remember what the org shipped, so future missions recall it. Idempotent:
        a project memory keyed to this mission is created once and updated on later ships."""
        from foundry_core.enums import MemoryType
        from foundry_core.models import Memory

        title = f"Shipped {mission.key}: {mission.title}"[:120]
        where = mission.project_path or mission.branch or "the repository"
        brief = (mission.requirements or mission.summary or "").strip().replace("\n", " ")[:400]
        body = f"Delivered at {where}. Brief: {brief or '(none)'}"
        try:
            existing = next(
                (m for m in await self.store.list_memories(mission.workspace_id) if m.title == title), None
            )
            if existing is not None:
                await self.store.add_memory(existing.model_copy(update={"body": body}))
                return
            await self.store.add_memory(Memory(
                id=new_ulid(), org_id=mission.org_id, workspace_id=mission.workspace_id,
                type=MemoryType.PROJECT, title=title, body=body,
            ))
        except Exception:  # pragma: no cover - memory is best-effort, never fail a ship on it
            pass

    async def _persist_skill(self, mission: Mission) -> None:
        """Autonomously grow the team's skill library: after a ship, distill ONE reusable skill the
        team applied and add it if it's novel (deduped by name). Best-effort — never fails a ship."""
        from foundry_core.enums import SkillCategory, SkillSource
        from foundry_core.models import Skill

        try:
            provider = await self._active_provider()
            brief = (mission.requirements or mission.summary or mission.title).strip()[:600]
            result = await provider.complete(
                system="You curate a software team's reusable skill library. Be terse.",
                prompt=(
                    f"The team just shipped: {mission.title}\n{brief}\n\n"
                    "Name ONE reusable, generalizable engineering skill the team applied that would "
                    "help future projects (not specific to this ticket). Reply EXACTLY as:\n"
                    "NAME: <2-4 word kebab-case name>\nDESC: <one sentence>\n"
                    "If nothing generalizable, reply exactly: NONE"
                ),
                purpose="skill", max_tokens=120,
            )
            text = (result.text or "").strip()
            if "NONE" in text.upper() and "NAME:" not in text.upper():
                return
            name = re.search(r"NAME:\s*(.+)", text, re.IGNORECASE)
            desc = re.search(r"DESC:\s*(.+)", text, re.IGNORECASE)
            if not name:
                return
            skill_name = re.sub(r"[^a-z0-9\- ]", "", name.group(1).strip().lower()).replace(" ", "-")[:40]
            if not skill_name:
                return
            existing = {s.name for s in await self.store.list_skills(mission.workspace_id)}
            if skill_name in existing:
                return
            await self.store.add_skill(Skill(
                id=new_ulid(), org_id=mission.org_id, workspace_id=mission.workspace_id,
                name=skill_name,
                description=(desc.group(1).strip() if desc else f"Applied while shipping {mission.key}."),
                category=SkillCategory.ENGINEERING, source=SkillSource.PROJECT,
                auto_invoke=True, installed=True, uses=0,
            ))
        except Exception:  # pragma: no cover - skill growth is best-effort
            pass

    async def _recall_skills(self, mission: Mission, role: AgentRoleKey) -> tuple[str, list]:
        """The auto-invoke skills relevant to this phase's role, formatted for the prompt, plus the
        skill objects (so the caller can record real usage). Keeps the org 'using its skills'."""
        try:
            skills = [s for s in await self.store.list_skills(mission.workspace_id)
                      if getattr(s, "auto_invoke", False) and getattr(s, "installed", True)]
        except Exception:  # pragma: no cover
            return "", []
        role_cat = {
            AgentRoleKey.PM: "product", AgentRoleKey.BA: "product",
            AgentRoleKey.BACKEND: "engineering", AgentRoleKey.FRONTEND: "engineering",
            AgentRoleKey.QA: "quality", AgentRoleKey.SECURITY: "security",
            AgentRoleKey.DEVOPS: "devops", AgentRoleKey.DESIGNER: "design",
        }.get(role)
        picked = [s for s in skills if _enum_value(s.category) == role_cat] or skills[:2]
        if not picked:
            return "", []
        lines = "\n".join(f"- {s.name}: {s.description}" for s in picked)
        return f"Apply these team skills where relevant:\n{lines}\n\n", picked

    async def _build_summary(self, mission: Mission) -> str:
        """A short, real description of what's been built (branch + files), so QA/review/CTO verdicts
        are grounded in the actual deliverable rather than guessing."""
        parts: list[str] = []
        if mission.branch:
            parts.append(f"branch {mission.branch}")
        path = mission.project_path
        if path:
            from .sandbox import LocalSandbox
            try:
                sb = await LocalSandbox.at_path(path)  # noqa: ASYNC240
                files = await sb.list_files()
                if files:
                    parts.append("files: " + ", ".join(files[:20]) + (" …" if len(files) > 20 else ""))
            except Exception:  # pragma: no cover - project dir may be absent
                pass
        return "; ".join(parts)

    async def _deliverable_context(self, mission_id: str, mission: Mission) -> str:
        """The REAL deliverable, for grounding QA/review/CTO verdicts: branch, agent steps, test
        result, file list AND the actual diff. Falls back to a file-name summary when the build facts
        aren't cached (e.g. after a restart). The instruction tells the agent to judge against THIS."""
        facts = self._build_facts.get(mission_id)
        if not facts:
            summary = await self._build_summary(mission)
            return (f"What has been built so far: {summary}\n"
                    "IMPORTANT: verify against the ACTUAL code in the repo; do not assume features exist.\n"
                    ) if summary else ""
        files = facts.get("files") or []
        steps = int(facts.get("steps", 0) or 0)
        tests = "passed" if facts.get("tests_passed") else "not run or failing"
        health = "OK" if facts.get("healthy", True) else f"⚠ {facts.get('why', '')}"
        diff = str(facts.get("diff") or "")
        # Generous budget so the whole small app is visible (judges run on large-context models); a
        # too-small excerpt made QA false-fail behavioural criteria as "not shown in the diff".
        diff_excerpt = diff if len(diff) <= _DIFF_BUDGET else diff[:_DIFF_BUDGET] + "\n… (diff truncated)"
        flist = ", ".join(files[:40]) + (" …" if len(files) > 40 else "")
        return (
            "What has ACTUALLY been built — judge STRICTLY against this real evidence:\n"
            f"- branch: {facts.get('branch', '(none)')}\n"
            f"- agent build steps: {steps} (0 means NO real work was done — a stub/scaffold only)\n"
            f"- tests: {tests}\n"
            f"- build health: {health}\n"
            f"- files ({len(files)}): {flist}\n"
            f"{self._qa_evidence_line(facts)}"
            f"--- actual diff (the real code) ---\n{diff_excerpt}\n--- end diff ---\n"
            "HOW TO JUDGE — read this carefully:\n"
            "1) EXISTENCE: the file list above is the COMPLETE, authoritative inventory of the repo — a "
            "file EXISTS iff it is in that list. NEVER report a file as 'missing'/'absent' when it is "
            "listed; the diff may be truncated, so absence from the diff is NOT absence from the code.\n"
            "2) RUNTIME BEHAVIOUR: the QA harness above actually SERVED and RAN the app. A smoke check "
            "shown as `pass` is RUNTIME PROOF that behaviour works (e.g. an endpoint really responded, "
            "the page really rendered). If a criterion is covered by a passing harness check, it is "
            "SATISFIED — do NOT fail it for 'no evidence' or 'not visible in the diff'. Only fail a "
            "criterion when the file is genuinely absent from the list, a harness check for it FAILED, "
            "or the diff plainly shows the code is wrong/incomplete. Never fail a criterion merely "
            "because its implementation was not in the (possibly truncated) diff excerpt.\n"
        )

    @staticmethod
    def _qa_evidence_line(facts: dict) -> str:
        """Render the deterministic QA-harness evidence for verdict grounding, if it ran. The checks
        are machine facts (the app really was served and these smoke checks really ran) — weigh them
        above your own reading of the diff."""
        ev = facts.get("qa_evidence")
        if not ev:
            return ""
        checks = ev.get("checks") or []
        shown = "; ".join(f"{c['name']}={c['status']}" for c in checks[:12]) or "(none)"
        degr = ("; ".join(ev.get("degradation") or []))[:300]
        lines = (
            f"- QA evidence harness: {ev.get('summary', '')}\n"
            f"  deterministic smoke checks — {shown}\n"
        )
        if ev.get("screenshots"):
            lines += f"  captured {len(ev['screenshots'])} screenshot artifact(s) of the running app\n"
        if degr:
            lines += f"  note: evidence was degraded ({degr}); weigh it accordingly\n"
        return lines

    async def _run_llm_phase(
        self, run_id: str, mission_id: str, phase: Phase, step_id: str, *, attempt: int = 1
    ) -> str:
        mission = await self.store.get_mission(mission_id)
        run = await self.store.get_run(run_id)
        if mission is None or run is None:
            return ""
        from .roles import role_system_prompt
        # Role-scoped system prompt keeps each agent in its lane (e.g. QA verifies, CTO reviews).
        system = role_system_prompt(_enum_value(phase.role)) or _SYSTEM.get(
            phase.role, "You are a helpful engineering agent.")
        recalled = await self._recall_memory(mission)
        verdict_ask = _VERDICT_ASK.get(phase.key, "")
        attempt_line = f"Attempt: {attempt}\n" if attempt > 1 else ""
        build_line = ""
        if phase.key in ("qa", "review", "cto.decision"):
            # On a resumed run, rebuild the grounding from the on-disk app first (else a wiped
            # in-memory context makes QA falsely fail present features). No-op in the normal flow.
            await self._ensure_build_facts(run_id, mission_id, mission)
            # Ground the verdict in the REAL deliverable — the actual diff, file list, agent steps and
            # test result — not a bare file-name summary. Without this the reviewer hallucinates features
            # that don't exist (M-157). Judge strictly against what is shown.
            build_line = await self._deliverable_context(mission_id, mission)
        skills_line, used_skills = await self._recall_skills(mission, phase.role)
        prompt = (
            f"{mission.title}\n\n"
            f"Summary: {mission.summary or '(none)'}\n"
            f"Requirements / brief:\n{mission.requirements or '(none)'}\n"
            f"{build_line}"
            f"{attempt_line}"
            f"{recalled}"
            f"{skills_line}"
            f"Phase: {phase.key}. Produce the {phase.purpose} output for this ticket."
            + (f"\n\n{verdict_ask}" if verdict_ask else "")
        )
        # Record real skill usage (the org actually uses its skills, and usage counts are honest).
        for sk in used_skills:
            with contextlib.suppress(Exception):
                await self.store.update_skill(sk.id, uses=getattr(sk, "uses", 0) + 1)
        # Route through this mission's team agent for the role, trying its models in order (failover).
        providers = await self._resolve_providers(mission, phase.role)
        served = providers[0]
        # Give reasoning phases room to finish; decision phases get the most so the trailing VERDICT
        # line is never truncated off (the M-157 bug, where a long QA analysis hit the 1024 cap before
        # emitting VERDICT: REWORK). _decide also re-elicits a missing verdict as a second safety net.
        max_toks = 4096 if phase.key in _VERDICT_VALID else 2048
        with tracing.span(
            "llm.call", {tracing.GEN_AI_SYSTEM: getattr(served, "name", type(served).__name__)}
        ) as call_span:
            result = await self._complete_failover(
                providers, run, mission, phase.role,
                system=system, prompt=prompt, purpose=phase.purpose, max_tokens=max_toks,
            )
            tracing.set_attributes(call_span, {
                tracing.GEN_AI_MODEL: result.model,
                tracing.GEN_AI_INPUT_TOKENS: result.tokens_in,
                tracing.GEN_AI_OUTPUT_TOKENS: result.tokens_out,
                tracing.ATTR_COST_CENTS: result.cost_cents,
            })
        await self.store.update_run(
            run_id,
            tokens_in=run.tokens_in + result.tokens_in,
            tokens_out=run.tokens_out + result.tokens_out,
            cost_cents=run.cost_cents + result.cost_cents,
            provider=getattr(served, "name", type(served).__name__),
            model=result.model,
        )
        r = await self.store.get_run(run_id)
        if r:
            await self._emit(
                r, mission, phase.role, phase.event_type, result.text,
                payload={"kind": "phase.output", "phase": phase.key, "model": result.model,
                         "tokensIn": result.tokens_in, "tokensOut": result.tokens_out,
                         "costCents": result.cost_cents},
            )
        return result.text

    async def _emit_note(
        self, run_id: str, mission_id: str, role: AgentRoleKey, text: str, *, kind: str = "transition"
    ) -> None:
        """Emit a short routing/consultation note onto the console (best-effort)."""
        run = await self.store.get_run(run_id)
        mission = await self.store.get_mission(mission_id)
        if run and mission:
            await self._emit(run, mission, role, "review", text, payload={"kind": f"route.{kind}"})

    async def _real_build(self, run_id: str, mission_id: str, phase: Phase, step_id: str) -> None:
        """Run the real sandbox dev loop: branch → apply change → test → commit → diff."""
        from . import devloop

        mission = await self.store.get_mission(mission_id)
        run = await self.store.get_run(run_id)
        if mission is None or run is None:
            return

        # Build event callbacks. ``who`` tags the emitting agent — empty for a single build, or
        # "<name> · <subtask>" for each parallel worker. ``role`` sets the event's role so the avatar
        # /colour matches the actual agent (a frontend worker shows as Frontend, not Backend).
        def _role_key(role: str):
            try:
                return AgentRoleKey(role)
            except ValueError:
                return phase.role

        def _make_cbs(who: str = "", role: str = ""):
            tag = who or _enum_value(phase.role).title()
            rk = _role_key(role) if role else phase.role

            async def on_tool(name: str, inp: dict, out: str) -> None:
                m2 = await self.store.get_mission(mission_id)
                r2 = await self.store.get_run(run_id)
                if m2 is None or r2 is None:
                    return
                label = {"fs_read": "read", "fs_write": "wrote", "fs_list": "list", "cmd_run": "ran",
                         "git_diff": "diff"}.get(name, name)
                cmd = inp.get("command")
                target = inp.get("path") or (" ".join(map(str, cmd)) if isinstance(cmd, list) else "")
                first = out.splitlines()[0][:70] if out else ""
                is_err = bool(out) and out.lstrip().lower().startswith("error")
                prefix = f"{who} — " if who else ""
                await self._emit(
                    r2, m2, rk, "error" if is_err else "code",
                    f"{prefix}{label} {target} · {first}".strip(),
                    payload={"kind": "tool.error" if is_err else "tool", "tool": name, "agent": who or None},
                )

            async def on_turn(step: int) -> None:
                m2 = await self.store.get_mission(mission_id)
                r2 = await self.store.get_run(run_id)
                if m2 is None or r2 is None:
                    return
                await self._emit(
                    r2, m2, rk, "status",
                    f"{tag} · thinking (step {step})…",
                    payload={"kind": "turn", "step": step, "agent": who or None},
                )

            return on_tool, on_turn

        on_tool, on_turn = _make_cbs()  # single-build callbacks (unchanged "Backend …" wording)

        # The build runs on the team's Backend agent's models, in order — if the first model errors
        # mid-build we fail over to the next (same policy as the reasoning phases).
        providers = await self._resolve_providers(mission, phase.role)
        kind = _enum_value(mission.project_kind)
        is_app = kind == "app"
        is_change_repo = kind == "change" and bool(mission.project_path)
        # Record where the deliverable lives BEFORE the (slow) build so the Details rail shows the
        # project folder immediately instead of only after the build finishes.
        if is_app and not mission.project_path:
            planned = devloop.default_project_path(await self._effective_projects_root(), mission)
            await self.store.update_mission(mission_id, project_path=planned)
            mission = await self.store.get_mission(mission_id) or mission
            await self._emit(run, mission, phase.role, "status",
                             f"Project folder · {planned}", payload={"kind": "project.path", "path": planned})

        # PARALLEL build is the DEFAULT: an app build fans out across BOTH backend and frontend agents,
        # each in its own git worktree, merged after. The planner tags each subtask with a role so a
        # FRONTEND part goes to a frontend agent and a BACKEND part to a backend agent (no more backend
        # agents styling UI). A `solo` label forces single-agent; it also falls back to single when a
        # task doesn't cleanly split.
        # GREENFIELD apps build SOLO by default. Parallel workers run in ISOLATED git worktrees and
        # can't see each other's files, so a from-scratch app fragments into placeholder shells +
        # duplicate entry files (each worker scaffolds its slice, none builds the coherent whole). One
        # capable agent that sees its own files as it goes produces a real, coherent app. Fan-out is
        # opt-IN for apps via a `parallel` label (best for splitting work across an EXISTING codebase).
        parallel_mode = is_app and self._wants_parallel(mission) and not self._is_solo(mission)
        # Recruit builders from every implementation role — (provider, name, role, agent) specs.
        all_specs: list = []
        if parallel_mode:
            for br in (AgentRoleKey.BACKEND, AgentRoleKey.FRONTEND):
                agents_r = await self._team_agents_for(mission, br)
                provs_r = await self._resolve_agent_providers(mission, br)
                for a, p in zip(agents_r, provs_r, strict=False):
                    all_specs.append((p, a.name, _enum_value(br), a))
        # Reuse the PM's cached plan (from the `plan` phase) so the pre-created tickets and the build's
        # subtasks line up exactly (same titles → same subtask_ids). Only decompose here as a safety
        # net — e.g. a run that resumed directly at build.api without going through `plan`.
        subtasks: list = list(self._plans.get(mission_id) or [])
        if parallel_mode and len(all_specs) >= 2 and not subtasks:
            planner = (await self._resolve_providers(mission, AgentRoleKey.CTO))[0]
            cap = min(len(all_specs), devloop.MAX_PARALLEL_SUBTASKS)
            subtasks = await devloop.decompose(mission, planner, cap)
        # Targeted rework: QA flagged specific failing parts → rebuild ONLY those (off the current
        # code in git; every untouched file stays), instead of regenerating the whole app each cycle.
        rework_targets = self._rework_targets.pop(mission_id, set())
        rework_note = self._rework_reason.pop(mission_id, "")  # QA/review/CTO's fix instructions → builders
        focused = [st for st in subtasks if st.title in rework_targets] if rework_targets else []
        targeted = bool(focused) and len(focused) < len(subtasks)
        if targeted:
            subtasks = focused
        # A targeted rework runs the parallel path even for a SINGLE failing part; a first build still
        # needs ≥2 parts to be worth fanning out.
        do_parallel = parallel_mode and len(all_specs) >= 2 and (len(subtasks) >= 2 or targeted)
        assigned_agents: list = []
        agent_specs: list = []
        if do_parallel:
            # Role-match subtasks → agents (same deterministic logic build_parallel uses), so the
            # agents we mark "on shift" are exactly the ones that will build.
            assignments = devloop._assign(subtasks, all_specs)
            subtasks = [st for st, _spec in assignments]
            agent_specs = [spec for _st, spec in assignments]
            assigned_agents = [spec[3] for _st, spec in assignments]
            for a in assigned_agents:
                await self.store.update_agent(a.id, status=AgentStatus.WORKING)
            # Reconcile the board against the ACTUAL assignment (creates any missing per-dev ticket,
            # sets its owner, moves it To Do → In Progress) so Tickets == Live Build while they build.
            start_slices = [
                {"subtask_id": st.title, "title": st.title, "role": spec[2], "agent_name": spec[1],
                 "instructions": getattr(st, "instructions", ""), "skills": list(getattr(st, "skills", [])),
                 "acceptance": list(getattr(st, "acceptance", [])),
                 "files": list(getattr(st, "files", [])), "depends_on": list(getattr(st, "depends_on", []))}
                for st, spec in zip(subtasks, agent_specs, strict=False)
            ]
            await self._tickets.on_build_start(mission, start_slices)
            who = ", ".join(f"{spec[1]} ({spec[2]})" for spec in agent_specs)
            if targeted:
                await self._emit_note(
                    run_id, mission_id, AgentRoleKey.CTO,
                    f"🎯 Targeted rework: rebuilding only the {len(subtasks)} failing part(s) — {who} — "
                    "off the current codebase; every other file stays untouched.",
                    kind="rework.targeted")
            else:
                await self._emit_note(
                    run_id, mission_id, AgentRoleKey.CTO,
                    f"🧭 Parallel build: split into {len(subtasks)} parts across {who} — each in an "
                    "isolated git worktree, role-matched, merged after.",
                    kind="parallel")
            # Stamp the build step with the per-role parallel breakdown NOW (before the build runs),
            # so the Live Build pipeline shows "Backend ×2 · Frontend ×1" while the agents work and
            # even if the build later fails mid-flight. spec[2] is the role; agent_specs holds one
            # unique agent per subtask. Solo builds never reach here → single-role avatar unchanged.
            counts: dict[str, int] = {}
            for spec in agent_specs:
                counts[spec[2]] = counts.get(spec[2], 0) + 1
            _order = {"backend": 0, "frontend": 1, "fullstack": 2}
            breakdown = [{"role": r, "count": n} for r, n in
                         sorted(counts.items(), key=lambda kv: (_order.get(kv[0], 9), kv[0]))]
            with contextlib.suppress(Exception):
                await self.store.update_step(step_id, detail="build-roles:" + json.dumps(breakdown))

        async def _cto_consult(question: str, subtask_title: str, agent_role: str) -> str:
            """A blocked builder asks the CTO instead of guessing — the CTO gives one decisive answer,
            surfaced on the console, and the builder is re-run with it. Best-effort: on any error the
            builder just proceeds with its own safe choice (never blocks the build)."""
            from .roles import role_system_prompt
            try:
                cto = (await self._resolve_providers(mission, AgentRoleKey.CTO))[0]
                brief = (mission.requirements or mission.summary or mission.title or "").strip()
                ask = (
                    f"A {agent_role} engineer building '{subtask_title}' for the project "
                    f"'{mission.title}' is BLOCKED and asks the CTO:\n\n{question}\n\n"
                    f"Project brief:\n{brief[:1500]}\n\n"
                    "Give a SHORT, decisive answer (2-4 sentences) telling them EXACTLY what to do — pick "
                    "one approach and state it plainly. No preamble, no options list.")
                res = await cto.complete(
                    system=role_system_prompt("cto") or "You are the CTO.", prompt=ask,
                    purpose="cto", max_tokens=400)
                answer = (res.text or "").strip()
                await self._emit_note(
                    run_id, mission_id, AgentRoleKey.CTO,
                    f"🧭 {subtask_title} asked the CTO: {question[:120]} → {answer[:200]}",
                    kind="cto.guidance")
                return answer
            except Exception:  # noqa: BLE001 — guidance is best-effort; the builder proceeds regardless
                return ""

        async def _one_build(provider):
            # Multi-project coordinated change (Approach A): edit each selected repo in turn, with the
            # whole working set as shared context. Single-project missions skip this entirely.
            targets = await self._project_targets(mission)
            if targets:
                return await self._build_multi_repo(mission, provider, targets, on_tool, on_turn)
            if is_app:
                if do_parallel:
                    # Fork-join: each role-matched agent builds a disjoint part (pre-decomposed).
                    result, sb, path = await devloop.build_parallel(
                        mission, provider, agent_specs, subtasks=subtasks,
                        projects_root=await self._effective_projects_root(), make_callbacks=_make_cbs,
                        # A targeted rework must NOT fall back to regenerating the whole app — that's the
                        # very behaviour we're removing. Rebuild just the failing slice(s).
                        allow_solo_fallback=not targeted,
                        # QA/review fix instructions → the builders, and a CTO escalation path for a
                        # blocked worker (ask instead of guessing).
                        rework_note=rework_note, cto_consult=_cto_consult,
                    )
                else:
                    # Greenfield: ONE agent builds the whole coherent project on disk. Generous step
                    # budget so it can create the full multi-file app (components, hooks, styles, entry)
                    # — not stop at a scaffold.
                    # Reconcile + start the single "build" story (creates it if the plan didn't), owned
                    # by the backend engineer, so the board shows In Progress while it builds.
                    solo_agent = await self._team_agent(mission, phase.role)
                    brief = (mission.requirements or mission.summary or mission.title or "").strip()
                    await self._tickets.on_build_start(mission, [{
                        "subtask_id": "build", "title": mission.title,
                        "role": _enum_value(phase.role), "instructions": brief,
                        "agent_name": getattr(solo_agent, "name", None)}])
                    result, sb, path = await devloop.build_from_mission(
                        mission, provider, projects_root=await self._effective_projects_root(),
                        on_event=on_tool, on_turn=on_turn, max_steps=40,
                    )
                await self.store.update_mission(mission_id, project_path=path)
                return result, sb
            if is_change_repo:
                # Real change inside the user's existing repo, on a fix/ branch.
                result, sb, _path = await devloop.change_in_repo(
                    mission, provider, on_event=on_tool, on_turn=on_turn,
                )
                return result, sb
            result, sb = await devloop.real_build(
                mission, provider, sandbox_root=self.sandbox_root,
                on_event=on_tool, on_turn=on_turn,
            )
            return result, sb

        # The role tries EACH of its models (self-healing); if all fail it RAISES so the engine's
        # error-recovery ladder (role → CTO → user) takes over instead of silently continuing.
        result = sb = None
        for i, provider in enumerate(providers):
            try:
                result, sb = await _one_build(provider)
                break
            except ProviderError as exc:  # a model failing over is recoverable — try the next
                if i + 1 < len(providers):
                    await self._emit(
                        run, mission, phase.role, "error",
                        f"{getattr(provider, 'model', 'model')} failed ({exc}); failing over to the next model",
                        payload={"kind": "model.failover"},
                    )
                    continue
                raise  # all models exhausted → escalate
        if result is None or sb is None:  # no providers resolved (shouldn't happen)
            raise ProviderError("build failed: no model available")

        # The build is done — release the parallel agents (the phase-role agent is released by the
        # step lifecycle) so they don't linger "on shift" through QA/review.
        for a in assigned_agents:
            await self.store.update_agent(a.id, status=AgentStatus.IDLE)

        self._builds[mission_id] = (sb, result.branch)
        await self.store.update_mission(mission_id, branch=result.branch)
        # Cache the ground-truth build facts + a health verdict for QA/review/CTO grounding and gating.
        healthy, why = _assess_build(result)
        # Story slices for the ticket board: one per parallel subtask (with its owning agent), or a
        # single implicit story for a solo build. Kept on the facts so the ticket consumer is fed the
        # real who/what without re-deriving it.
        story_slices: list[dict] = []
        if do_parallel and agent_specs:
            for st, spec in zip(subtasks, agent_specs, strict=False):
                story_slices.append({
                    "subtask_id": st.title, "title": st.title, "role": spec[2],
                    "agent_name": spec[1], "files": list(st.files),
                })
        else:
            solo_team = await self._team_agents_for(mission, phase.role)
            solo_name = solo_team[0].name if solo_team else _enum_value(phase.role).title()
            story_slices.append({
                "subtask_id": "build", "title": mission.title,
                "role": _enum_value(phase.role), "agent_name": solo_name,
                "files": list(result.files),
            })
        self._build_facts[mission_id] = {
            "files": list(result.files), "tests_passed": result.tests_passed, "steps": result.steps,
            "diff": result.diff, "summary": result.summary, "branch": result.branch,
            "healthy": healthy, "why": why, "subtasks": story_slices,
            # net writes in THIS build (0 on a rework ⇒ no progress → the stuck-loop guard fires)
            "files_written": int(getattr(result, "files_written", 0) or 0),
        }
        # Coordinated multi-repo change: attach per-repo detail (branch/files/diff/healthy) so review,
        # QA, and the UI judge the WHOLE change set, not just one repo.
        if mission_id in self._multi_repo:
            self._build_facts[mission_id]["repos"] = self._multi_repo[mission_id]
        # (The build step's parallel role breakdown was stamped up-front, when the plan was decided,
        # so it shows during the build and survives a mid-build failure — see the do_parallel block.)
        run = await self.store.get_run(run_id) or run
        await self.store.update_run(
            run_id,
            tokens_in=run.tokens_in + result.tokens_in,
            tokens_out=run.tokens_out + result.tokens_out,
            cost_cents=run.cost_cents + result.cost_cents,
            provider=getattr(provider, "name", type(provider).__name__),
            model=getattr(provider, "model", None),
        )
        run = await self.store.get_run(run_id) or run
        diff_preview = result.diff if len(result.diff) <= 4000 else result.diff[:4000] + "\n… (truncated)"
        health_tag = "" if healthy else f" · ⚠ {why}"
        if is_app:
            headline = (f"Built {len(result.files)} file(s) in {mission.project_path or 'the project dir'} "
                        f"· {result.steps} agent steps{health_tag}")
        else:
            status = "✓ tests green" if result.tests_passed else "✗ tests failing"
            headline = (f"Committed to {result.branch} · {len(result.files)} file(s) · "
                        f"{result.steps} agent steps · {status}{health_tag}")
        await self._emit(
            run, mission, phase.role, "code", headline,
            payload={"kind": "diff", "branch": result.branch, "files": result.files,
                     "testsPassed": result.tests_passed, "steps": result.steps,
                     "healthy": healthy, "diff": diff_preview,
                     "projectPath": mission.project_path if (is_app or is_change_repo) else None},
        )

        # Gather deterministic QA evidence (screenshots + smoke checks) against the real build, so the
        # QA/review verdicts are grounded in reality. Best-effort: never raises, never fails the run.
        if is_app or is_change_repo:
            mission = await self.store.get_mission(mission_id) or mission
            with contextlib.suppress(Exception):
                await self._run_qa_evidence(run_id, mission_id, mission)
        # Board: ensure Stories for this build and move them to QA (who/what/why). Isolated.
        await self._tickets.sync_build(mission, self._build_facts.get(mission_id, {}))

    async def _ensure_build_facts(self, run_id: str, mission_id: str, mission: Mission) -> None:
        """On a RESUMED run the in-memory build facts are gone (a restart wipes ``_build_facts``), so a
        QA/review that resumes without rebuilding would grade on a thin file-name summary — and falsely
        fail features that are actually present (the M-172 ``/api/stats`` false-fail). Reconstruct the
        grounding from the REAL on-disk deliverable: files + diff from the repo root, a health verdict,
        and fresh evidence from the harness (which serves the actual running app). No-op when real facts
        are already cached (normal flow) or there's nothing on disk to read."""
        from types import SimpleNamespace

        from .sandbox import LocalSandbox
        facts = self._build_facts.get(mission_id)
        if facts and facts.get("files"):
            return
        path = mission.project_path
        if not path or not _dir_exists(path):
            return
        try:
            sb = await LocalSandbox.at_path(path)
            if not await sb.has_repo():
                return
            base = await sb.root_sha()
            files = await sb.changed_files(base)
            diff = await sb.diff(base)
        except Exception:  # noqa: BLE001 — grounding reconstruction must never fail the run
            return
        # The app exists on disk ⇒ real work (steps/files_written > 0 so the stub + no-progress gates
        # don't misfire); tests weren't run this session, so leave that False (advisory only).
        healthy, why = _assess_build(
            SimpleNamespace(files=files, steps=1, summary="", files_written=len(files)))
        self._builds[mission_id] = (sb, "main")
        self._build_facts[mission_id] = {
            "files": files, "tests_passed": False, "steps": 1, "diff": diff,
            "summary": f"resumed: {len(files)} file(s) already on disk", "branch": "main",
            "healthy": healthy, "why": why, "subtasks": [], "files_written": len(files),
        }
        with contextlib.suppress(Exception):  # fold in real screenshots + smoke checks (serves the app)
            await self._run_qa_evidence(run_id, mission_id, mission)

    async def _run_qa_evidence(self, run_id: str, mission_id: str, mission: Mission) -> None:
        """Run the deterministic QA evidence harness against the built project and fold its evidence
        into the ground-truth build facts, so QA/review verdicts are grounded in real screenshots and
        smoke checks (plan 06). Best-effort: the harness never raises and never fails a run — on any
        problem we record that visual evidence was unavailable and carry on. It is grounding INPUT to
        the verdict, never authoritative (verdict-fail-safe invariant)."""
        from . import qa_harness
        from .config import get_settings

        project_path = mission.project_path
        if not project_path or not _dir_exists(project_path):
            return  # only a real on-disk build (app / change-in-repo) has something to serve
        settings = get_settings()
        criteria = await self._acceptance_criteria(mission_id)
        ev = await qa_harness.run(
            workspace_id=mission.workspace_id, mission_id=mission_id, run_id=run_id,
            project_path=project_path, criteria=criteria,
            total_timeout_s=settings.qa_total_timeout_s, video=settings.qa_video,
        )
        for art in ev.artifacts:
            with contextlib.suppress(Exception):
                await self.store.add_artifact(art)
        shots = [a.id for a in ev.artifacts if _enum_value(a.kind) == "screenshot"]
        facts = self._build_facts.setdefault(mission_id, {})
        # Preserve the per-CRITERION signal (id, criterion_id, severity) + counts, so the QA reducer
        # (_classify_qa) can distinguish a MAJOR failure (reopen) from a PARTIAL one (forward + follow-up).
        crit_by_id = {c.id: c for c in criteria}
        checks_out: list[dict] = []
        crit_total = crit_failed = 0
        for c in ev.checks:
            sev = getattr(crit_by_id.get(c.criterion_id), "severity", None) if c.criterion_id else None
            checks_out.append({"id": c.id, "name": c.name, "status": c.status,
                               "detail": c.detail, "criterion_id": c.criterion_id, "severity": sev})
            if c.criterion_id is not None:
                crit_total += 1
                if c.status == "fail":
                    crit_failed += 1
        facts["qa_evidence"] = {
            "rung": ev.rung, "quality": ev.evidence_quality, "summary": ev.summary,
            "checks": checks_out, "crit_total": crit_total, "crit_failed": crit_failed,
            "degradation": ev.degradation, "screenshots": shots,
        }
        run = await self.store.get_run(run_id)
        m = await self.store.get_mission(mission_id)
        if run and m:
            tail = f" · {len(shots)} screenshot(s)" if shots else ""
            await self._emit(
                run, m, AgentRoleKey.QA, "qa",
                f"QA evidence · {ev.summary}{tail}",
                payload={"kind": "qa.evidence", **facts["qa_evidence"]},
            )

    async def _acceptance_criteria(self, mission_id: str) -> list:
        """Best-effort acceptance criteria for the QA harness, parsed from the spec phase output.
        Fail-soft: returns [] when the spec has no machine-readable ``foundry-criteria`` block."""
        from .specdoc import extract_criteria
        try:
            events = await self.store.list_events(mission_id=mission_id, limit=400)
        except Exception:  # noqa: BLE001 — evidence grounding must never break a build
            return []
        spec_text = ""
        for e in events:  # events are oldest→newest; the latest spec output wins
            payload = getattr(e, "payload", None) or {}
            if payload.get("kind") == "phase.output" and payload.get("phase") == "spec":
                spec_text = e.text or spec_text
        try:
            return extract_criteria(spec_text) if spec_text else []
        except Exception:  # noqa: BLE001
            return []

    def _build_is_healthy(self, mission_id: str) -> tuple[bool, str]:
        """Ground-truth health of the latest build for this mission. When unknown (e.g. after a
        restart cleared the cache), assume healthy so we don't block on missing data — the LLM QA
        still runs. Returns (healthy, reason)."""
        facts = self._build_facts.get(mission_id)
        if not facts:
            return True, ""
        return bool(facts.get("healthy", True)), str(facts.get("why", ""))

    async def _open_pr_if_built(self, run_id: str, mission_id: str) -> bool:
        """Push the built work and open a PR. Returns True when the ship can complete (pushed, or
        nothing to push), or False when it NEEDS THE USER'S DECISION (no token, or the push was
        rejected and the user must authorize a force-push / pick a different branch). On False the
        caller re-opens the merge gate; the sandbox for a real on-disk project is preserved so the
        retry can push again."""
        from . import devloop
        from .connectors import PushRejected
        from .sandbox import LocalSandbox

        mission = await self.store.get_mission(mission_id)
        run = await self.store.get_run(run_id)
        meta = self._merge_meta.pop(mission_id, {})
        repo = meta.get("repo") or None
        force = bool(meta.get("force"))  # only true when the USER authorized it at the gate
        requested_repo = repo or self._connector.repo  # did the user (or env) ask to push somewhere?

        entry = self._builds.pop(mission_id, None)
        if entry is None:
            # The in-process sandbox is gone (a restart, or a re-gate after a rejected push).
            # For a real on-disk project we reopen it and push again.
            if mission is None or not mission.project_path:
                if requested_repo and mission is not None and run is not None:
                    # A push was wanted but the build artifact (a throwaway sandbox) is gone — don't
                    # falsely "ship"; surface it so the user re-runs to rebuild.
                    await self._emit(run, mission, AgentRoleKey.DEVOPS, "error",
                                     "The built work is no longer available (it was a temporary "
                                     "sandbox, lost on restart). Re-run the mission to rebuild, then push.",
                                     payload={"kind": "pr.error"})
                    return False
                return True  # genuinely nothing to push → local ship
            sb = await LocalSandbox.at_path(mission.project_path)
            branch = mission.branch or "main"
        else:
            sb, branch = entry
        push_branch = meta.get("branch") or branch
        # A real on-disk project (app build or change-in-repo) must never be deleted; only the
        # throwaway demo temp sandbox is.
        keep_dir = mission is not None and bool(mission.project_path)
        connector = await self._connector_for(repo, force)
        needs_user = False  # set when the push needs a user decision (re-gate) → preserve the sandbox
        try:
            if requested_repo and not connector.token:
                # The user wants to push but no GitHub token is connected — a real, actionable error.
                if run and mission:
                    await self._emit(
                        run, mission, AgentRoleKey.DEVOPS, "error",
                        f"Can't push to {requested_repo}: no GitHub token connected. Connect GitHub on "
                        "the Integrations page, then re-approve, to push the branch & open a PR.",
                        payload={"kind": "pr.error", "repo": requested_repo, "needsToken": True},
                    )
                needs_user = True
                return False  # needs the user to connect GitHub → re-gate
            pr = await devloop.open_pr(sb, mission, push_branch, connector)  # type: ignore[arg-type]
            # Record the ACTUAL pushed branch (+ PR url) so Details reflects reality, never a stale one.
            actual = getattr(pr, "branch", push_branch)
            if pr.dry_run:
                await self.store.update_mission(mission_id, pr_url=pr.url)
                if run and mission:  # No repo was requested → nothing was pushed. Say so plainly.
                    await self._emit(run, mission, AgentRoleKey.DEVOPS, "deploy",
                                     "Merge approved locally — no repo connected, so nothing was pushed. "
                                     "Add a repo at the gate to push & open a PR.",
                                     payload={"kind": "pr.skipped"})
            else:
                await self.store.update_mission(mission_id, pr_url=pr.url, branch=actual)
                if run and mission:
                    recovery = getattr(pr, "recovery", "")
                    if recovery:  # e.g. an authorized force-push — say what happened
                        await self._emit(run, mission, AgentRoleKey.DEVOPS, "review", f"🛠 {recovery}",
                                         payload={"kind": "route.rebuild"})
                    pushed_only = getattr(pr, "state", "") == "pushed"
                    text = (f"Pushed {actual} to {pr.url}" if pushed_only
                            else f"Pushed {actual} and opened pull request {pr.url}")
                    await self._emit(run, mission, AgentRoleKey.DEVOPS, "deploy", text,
                                     payload={"kind": "pr.opened", "url": pr.url, "branch": actual,
                                              "dryRun": False})
            return True
        except PushRejected as exc:
            # The push to the chosen branch was rejected and no force was authorized. This is a USER
            # decision — surface it and re-open the gate; never switch branches or force on our own.
            if run and mission:
                await self._emit(run, mission, AgentRoleKey.DEVOPS, "error", str(exc),
                                 payload={"kind": "pr.rejected", "branch": exc.branch})
            needs_user = True
            return False
        except Exception as exc:
            # A credential/permission/repo problem. Surface it and re-open the gate so the user can
            # fix it (token / repo / branch) and re-approve — never push somewhere else on our own.
            if run and mission:
                await self._emit(run, mission, AgentRoleKey.DEVOPS, "error",
                                 f"Push / PR failed for {requested_repo or push_branch}: {exc}",
                                 payload={"kind": "pr.error"})
            needs_user = True
            return False
        finally:
            if needs_user:
                # The gate will re-open for the user's decision — KEEP the built work so the retry can
                # push it (don't destroy the only copy). The retry re-reads repo/branch/force meta.
                self._builds[mission_id] = (sb, branch)
            elif not keep_dir:
                await sb.destroy()  # type: ignore[attr-defined]

    async def _discard_build(self, mission_id: str) -> None:
        """Drop and destroy a throwaway sandbox that was PRESERVED across re-gates, when we give up
        on the push (reject / cap reached). A real on-disk project is never deleted."""
        entry = self._builds.pop(mission_id, None)
        if entry is None:
            return
        sb, _ = entry
        mission = await self.store.get_mission(mission_id)
        if mission is not None and mission.project_path:
            return  # real project — keep it
        try:
            await sb.destroy()  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - best-effort cleanup
            pass

    async def _await_ship_approval(
        self, run_id: str, mission_id: str, step_id: str, detail: str
    ) -> str:
        """Raise a merge-approval blocker and suspend until the user resolves it; return the decision
        ('approve'/'reject'). Reused for the first approval AND for re-asking after a rejected push
        (so the user can authorize a force-push or pick a different branch)."""
        mission = await self.store.get_mission(mission_id)
        run = await self.store.get_run(run_id)
        blocker = Blocker(
            id=new_ulid(), org_id=mission.org_id, workspace_id=mission.workspace_id,
            mission_id=mission.id, kind=BlockerKind.APPROVAL, severity=BlockerSeverity.WARN,
            detail=detail, created_at=_now(),
        )
        await self.store.add_blocker(blocker)
        await self._notifier.on_blocker(blocker, mission)
        await self.store.update_step(step_id, status=StepStatus.GATED)
        await self.store.update_run(run_id, status=RunStatus.BLOCKED)
        await self.store.update_mission(mission_id, is_blocked=True)
        await self._emit(
            run, mission, AgentRoleKey.DEVOPS, "deploy", f"⏸ {detail}",
            payload={"kind": "gate.awaiting", "gate": ApprovalGate.MERGE.value, "blockerId": blocker.id},
        )
        gate_attrs = {
            tracing.ATTR_GATE: ApprovalGate.MERGE.value,
            tracing.ATTR_BLOCKER_KIND: BlockerKind.APPROVAL.value,
        }
        with tracing.span("gate.wait", gate_attrs) as gate_span:
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[str] = loop.create_future()
            self._pending[blocker.id] = fut
            waited_from = time.monotonic()
            decision = await fut  # ← suspended here until resolve_blocker fires
            tracing.set_attributes(
                gate_span, {tracing.ATTR_WAIT_SECONDS: round(time.monotonic() - waited_from, 3)}
            )
        return decision

    async def _ship_gate(self, run_id: str, mission_id: str, step_id: str) -> bool:
        mission = await self.store.get_mission(mission_id)
        run = await self.store.get_run(run_id)
        if mission is None or run is None:
            return False

        autonomous = mission.autonomy is AutonomyLevel.AUTONOMOUS or mission.autonomy == "autonomous"
        # Defense-in-depth: never AUTO-approve a merge for a build that didn't pass ground-truth. The
        # routing already blocks such a build before ship, but if one ever reaches here, require the
        # human (don't auto-merge a stub). A healthy build in autonomous mode still auto-approves once.
        healthy, why = self._build_is_healthy(mission_id)
        detail = ("Reviews passed. Awaiting your approval to merge." if healthy
                  else f"⚠ The build did not pass ground-truth checks ({why}). Approve only if you're sure.")
        regates = 0
        first = True
        while True:
            if first and autonomous and healthy:
                decision = ApprovalDecision.APPROVE.value  # autonomous auto-approves the FIRST merge
                await self._emit(run, mission, AgentRoleKey.DEVOPS, "deploy",
                                 "Auto-approved the merge gate (autonomous)")
            else:
                # Non-autonomous, OR autonomous whose push was rejected: the user must decide. (Even
                # an autonomous mission can't force-push or switch branches on its own.)
                decision = await self._await_ship_approval(run_id, mission_id, step_id, detail)
            first = False

            mission = await self.store.get_mission(mission_id) or mission
            run = await self.store.get_run(run_id) or run
            if decision == ApprovalDecision.REJECT.value:
                await self.store.update_step(step_id, status=StepStatus.BLOCKED)
                await self.store.update_run(run_id, status=RunStatus.FAILED, finished_at=_now())
                await self.store.update_mission(mission_id, stage=MissionStage.REVIEW, is_blocked=False)
                # Release the DevOps agent (and the team) so no one is left "working" after a reject.
                await self._reset_agents_idle(mission.workspace_id)
                await self._discard_build(mission_id)  # drop the preserved throwaway sandbox, if any
                await self._emit(run, mission, AgentRoleKey.CTO, "review",
                                 "Merge rejected — returned to review", payload={"kind": "gate.rejected"})
                return False

            await self.store.update_run(run_id, status=RunStatus.RUNNING)
            await self.store.update_mission(mission_id, is_blocked=False)
            pushed_ok = await self._open_pr_if_built(run_id, mission_id)
            if pushed_ok:
                # Merged → close the board NOW (move every Story + the Epic to Done). Done here, at the
                # guaranteed merge-success point, so tickets always reach Done on ship regardless of how
                # the outer loop finalizes. on_shipped is idempotent, so the loop's call is a harmless backstop.
                merged_m = await self.store.get_mission(mission_id)
                if merged_m is not None:
                    await self._tickets.on_shipped(merged_m)
                await self._finish_step(step_id, run_id, mission_id, PHASES[-1],
                                        text="Merge approved — pipeline complete")
                return True

            # The push needs the user's decision (rejected / no token). Re-open the gate so they can
            # authorize a force-push or choose a different branch — never resolved autonomously.
            regates += 1
            if regates > MAX_PUSH_REGATES:
                await self.store.update_step(step_id, status=StepStatus.BLOCKED)
                await self.store.update_run(run_id, status=RunStatus.FAILED, finished_at=_now())
                await self.store.update_mission(mission_id, stage=MissionStage.REVIEW, is_blocked=False)
                await self._reset_agents_idle(mission.workspace_id)
                await self._discard_build(mission_id)  # drop the preserved throwaway sandbox, if any
                await self._emit(run, mission, AgentRoleKey.CTO, "error",
                                 "The push kept failing after several attempts — resolve the repo/branch "
                                 "and re-run when ready.", payload={"kind": "needs.user", "phase": "ship"})
                return False
            detail = ("The push was rejected. Authorize a force-push to overwrite that branch, or pick a "
                      "different branch, then approve again.")

    # ---- step / event helpers ---------------------------------------------------
    async def _begin_step(self, run: Run, phase: Phase) -> Step:
        step = Step(
            id=new_ulid(), run_id=run.id, phase=phase.key, title=phase.title,
            agent_role=phase.role, status=StepStatus.ACTIVE, started_at=_now(),
        )
        await self.store.add_step(step)
        mission = await self.store.get_mission(run.mission_id)
        # The team's agent for this role is now actively working — reflected on Team/Command Center.
        await self._set_agent_status(phase.role, AgentStatus.WORKING, run.workspace_id, mission)
        if mission:
            await self._emit(run, mission, phase.role, phase.event_type,
                             f"{phase.title} — started",
                             payload={"kind": "step.transition", "phase": phase.key, "stepStatus": "active"})
        return step

    async def _finish_step(
        self, step_id: str, run_id: str, mission_id: str, phase: Phase, *,
        text: str | None = None, detail: str | None = None
    ) -> None:
        fields: dict[str, object] = {"status": StepStatus.DONE, "finished_at": _now()}
        if detail is not None:
            fields["detail"] = detail  # e.g. "verdict:REWORK" — lets resume tell passed from unpassed
        step = await self.store.update_step(step_id, **fields)
        run = await self.store.get_run(run_id)
        mission = await self.store.get_mission(mission_id)
        if run and mission:
            # The team agent that owned this phase is done — back to idle, and credit its real work.
            await self._set_agent_status(phase.role, AgentStatus.IDLE, run.workspace_id, mission)
            if phase.key == "build.api" and mission.branch:
                await self._bump_agent_stat(phase.role, "prs", run.workspace_id, mission=mission)
            elif phase.key == "review":
                await self._bump_agent_stat(phase.role, "reviews", run.workspace_id, mission=mission)
            await self._emit(run, mission, phase.role, phase.event_type,
                             text or f"{phase.title} — done",
                             payload={"kind": "step.transition", "phase": phase.key, "stepStatus": "done"})
        _ = step

    async def _emit(
        self, run: Run, mission: Mission, role: AgentRoleKey, etype: str, text: str,
        *, payload: dict | None = None,
    ) -> Event:
        event = Event(
            id=new_ulid(), run_id=run.id, mission_id=mission.id, workspace_id=mission.workspace_id,
            agent_role=role, type=etype, text=text, payload=payload or {}, ts=_now(),
        )
        await self.store.add_event(event)
        await self.bus.publish(event)
        return event

    async def _record_approval(
        self, blocker: Blocker, decision: ApprovalDecision, actor: str | None, note: str | None
    ) -> None:
        approval = Approval(
            id=new_ulid(), mission_id=blocker.mission_id, blocker_id=blocker.id,
            gate=ApprovalGate.MERGE, decision=decision, actor=actor, note=note,
            created_at=_now(), decided_at=_now(),
        )
        # Approvals live in the store's event trail for Phase 1; a dedicated table lands with Postgres.
        m = await self.store.get_mission(blocker.mission_id)
        if m:
            await self.store.add_event(Event(
                id=new_ulid(), mission_id=m.id, workspace_id=m.workspace_id,
                agent_role=AgentRoleKey.CEO, type="review",
                text=f"Human {decision.value}d the merge gate",
                payload={"kind": "approval.decided", "gate": "merge",
                         "decision": decision.value, "approvalId": approval.id},
                ts=_now(),
            ))

    async def _fail(self, run_id: str, mission_id: str, message: str) -> None:
        await self.store.update_run(run_id, status=RunStatus.FAILED, finished_at=_now(), error=message)
        # The phase that failed left its step ACTIVE — mark any active/gated steps BLOCKED so the
        # pipeline doesn't show a step spinning forever after the run failed.
        for s in await self.store.list_steps(run_id):
            if _enum_value(s.status) in ("active", "gated"):
                await self.store.update_step(s.id, status=StepStatus.BLOCKED, finished_at=_now())
        mission = await self.store.get_mission(mission_id)
        run = await self.store.get_run(run_id)
        if mission and run:
            await self._reset_agents_idle(mission.workspace_id)
            await self._emit(run, mission, AgentRoleKey.DEVOPS, "error", f"Run failed: {message}",
                             payload={"kind": "run.failed"})

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
