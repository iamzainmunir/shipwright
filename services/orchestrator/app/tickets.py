"""Built-in ticket board consumer (v2 Phase 5 — plan 05).

Translates the run engine's lifecycle into ticket actions on Shipwright's own Jira-like board: one
**Epic** per mission, one **Story** per build subtask, one **Bug** per QA finding. Every transition
ALWAYS records who/what/why (the CEO-story requirement, enforced here, not in prompts).

**Observer rule (plan 05 §Observer):** a ticket-consumer bug must NEVER fail a run. Ticket writes go
to their own store transactions, so a failure here rolls back only the ticket write and is surfaced
as a ``sync_error`` ticket event — the engine's own writes already committed independently. Every
public method is wrapped so it swallows and records errors instead of raising into the pipeline.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from foundry_core.enums import TicketEventKind, TicketKind, TicketLinkType, TicketStatus
from foundry_core.ids import new_ulid
from foundry_core.models import Mission, Ticket, TicketEvent, TicketLink

# A sink notified after every ticket event is written — the Jira mirror hooks in here so the
# consumer stays mirror-agnostic. It must be best-effort (never raise into the consumer).
TicketEventSink = Callable[[Ticket, TicketEvent], Awaitable[None]]

# Definition of done shown on every Story (plan 05 §1).
_DOD = ["Build passes", "QA verdict passes", "Review approved"]


def _now() -> datetime:
    return datetime.now(UTC)


class TicketService:
    """Derives board tickets from lifecycle calls. Stateless beyond the injected store; safe to
    construct per engine instance and call from anywhere in the pipeline."""

    def __init__(self, store, *, sink: TicketEventSink | None = None) -> None:
        self.store = store
        self._sink = sink

    # ---- feature gate -----------------------------------------------------------
    async def enabled(self, workspace_id: str) -> bool:
        """Effective toggle: the workspace Settings → Features flag wins; if unset, the
        ``FOUNDRY_TICKETS`` config default applies. Off ⇒ the consumer is a no-op (domain events
        still flow to the run console); re-enabling reconciles via :meth:`backfill`."""
        from .config import get_settings
        default = get_settings().tickets_enabled
        try:
            policy = await self.store.get_settings(workspace_id)
            return bool((policy.features or {}).get("tickets", default))
        except Exception:  # noqa: BLE001 — never let a settings read break the pipeline
            return default

    # ---- public lifecycle hooks (each is failure-isolated) ----------------------
    async def ensure_epic(self, mission: Mission, *, criteria: list | None = None) -> Ticket | None:
        """Create the mission's Epic once (idempotent). Returns the Epic, or None if disabled/failed."""
        if not await self.enabled(mission.workspace_id):
            return None
        try:
            return await self._ensure_epic(mission, criteria)
        except Exception as exc:  # noqa: BLE001
            await self._record_sync_error(None, f"ensure_epic: {exc}")
            return None

    async def sync_build(self, mission: Mission, facts: dict) -> None:
        """After a build: ensure the Epic + a Story per subtask, and move each Story to QA with a
        comment naming the files changed, agent, and commit branch (who/what/why)."""
        if not await self.enabled(mission.workspace_id):
            return
        with contextlib.suppress(Exception):
            await self._sync_build(mission, facts)

    async def _upsert_story(self, mission: Mission, epic: Ticket, sl: dict,
                            created_by: tuple[str, str] | None = None) -> Ticket:
        """Create the Story for a planned/built subtask if it doesn't exist yet, else reuse it — and
        keep its owner + description in sync with what the build actually assigned. One code path so a
        story looks identical whether it was created at PLAN time (To Do) or reconciled at BUILD start.
        ``created_by`` names who filed it (the PM at plan time); defaults to the assigned engineer.

        A slice carries: subtask_id, title, role, agent_name, instructions, skills, files, depends_on."""
        sid = sl.get("subtask_id")
        role = sl.get("role") or "backend"
        name = sl.get("agent_name")
        # A ready-to-work ticket (Definition of Ready): what to do (instructions), how success is
        # checked (acceptance criteria — the dev builds to these, QA verifies them), the standard DoD,
        # the skills it needs, and the files it owns.
        desc = {"goal": sl.get("instructions") or sl.get("title") or mission.title,
                "acceptance": sl.get("acceptance") or [], "dod": _DOD,
                "skills": sl.get("skills") or [], "owned_paths": sl.get("files") or []}
        existing = await self.store.find_ticket(
            mission_id=mission.id, kind=TicketKind.STORY, subtask_id=sid)
        if existing is not None:
            # Fill in the owner once the build assigns a specific engineer (plan time may not know it).
            if name and not existing.agent_name:
                existing = await self.store.update_ticket(existing.id, agent_name=name)
            return existing
        story = await self._new_ticket(
            mission, TicketKind.STORY, title=sl.get("title") or mission.title,
            parent_id=epic.id, subtask_id=sid, description=desc,
            status=TicketStatus.TODO, agent_name=name, agent_role=role,
            labels=[f"role:{role}"] + ([f"agent:{role}-{_slug(name)}"] if name else []))
        # Who filed the ticket: the PM at plan time (created_by), else the assigned engineer, else PM.
        creator = created_by or ((name, role) if name else ("PM", "pm"))
        await self._add_event(story, TicketEventKind.CREATED, actor=creator,
                              body={"reason": "Planned task", "assignee": name, "role": role})
        return story

    async def _link_deps(self, mission: Mission, by_key: dict[str, str], slices: list[dict]) -> None:
        """Wire typed BLOCKS edges: a task blocks each task that depends on it (a story→story DAG)."""
        for sl in slices:
            frm = by_key.get(sl.get("subtask_id"))
            for dep in sl.get("depends_on") or []:
                to = by_key.get(dep)
                if frm and to and frm != to:
                    with contextlib.suppress(Exception):
                        await self.store.add_ticket_link(TicketLink(
                            id=new_ulid(), workspace_id=mission.workspace_id,
                            from_ticket=to, to_ticket=frm, link_type=TicketLinkType.BLOCKS))

    async def plan_stories(self, mission: Mission, slices: list[dict],
                           created_by: tuple[str, str] | None = None) -> None:
        """PM planning phase: create one Story per planned task UP FRONT (in To Do), assigned to its
        engineer with a real description, plus the dependency links between them — so the board shows
        the PM's actual breakdown (who builds what, in what order) the moment planning finishes.
        ``created_by`` names the PM who filed them (e.g. Humna)."""
        if not await self.enabled(mission.workspace_id):
            return
        with contextlib.suppress(Exception):
            epic = await self._ensure_epic(mission, None)
            by_key = {sl.get("subtask_id"): (await self._upsert_story(mission, epic, sl, created_by)).id
                      for sl in slices}
            await self._link_deps(mission, by_key, slices)

    async def on_build_start(self, mission: Mission, slices: list[dict]) -> None:
        """A build started → reconcile the board against the subtasks ACTUALLY being built and move
        each into In Progress. Creates any story the plan didn't (e.g. the build re-decomposed on a
        resume), assigns the real engineer, and links dependencies — so the Tickets board can never
        diverge from the Live Build (the 'tickets and live screens not aligning' bug). Fixes 'In
        Progress empty while eng working' by transitioning To Do/Reopened → In Progress here."""
        if not await self.enabled(mission.workspace_id):
            return
        with contextlib.suppress(Exception):
            epic = await self._ensure_epic(mission, None)
            by_key: dict[str, str] = {}
            for sl in slices:
                story = await self._upsert_story(mission, epic, sl)
                by_key[sl.get("subtask_id")] = story.id
                # Any non-terminal story is In Progress while its engineer builds — including a rework
                # cycle (QA/In Review bounced it back to the engineer). Only DONE (shipped) stays put,
                # so the board tracks the Live Build instead of getting stuck in QA during rework.
                if str(story.status) != TicketStatus.DONE and str(story.status) != TicketStatus.IN_PROGRESS:
                    actor = (sl.get("agent_name") or "Engineer", sl.get("role") or "backend")
                    reason = ("Build started" if str(story.status) in (TicketStatus.TODO, TicketStatus.REOPENED)
                              else "Reworking after QA")
                    await self._transition(story, TicketStatus.IN_PROGRESS, actor=actor,
                                           reason=reason, kind=TicketEventKind.TRANSITIONED)
            await self._link_deps(mission, by_key, slices)
            # A QA-filed bug is fixed by reworking the story it blocks — so during the rebuild, hand the
            # bug to that story's engineer and move it In Progress. It's now clearly being worked (not
            # stranded in To Do); it closes when QA re-passes / the mission ships.
            for bug in await self._open_bugs(mission.id):
                if str(bug.status) not in (TicketStatus.TODO, TicketStatus.REOPENED):
                    continue
                owner = await self._blocked_story_owner(bug)
                if owner and owner[0] and owner[0] != bug.agent_name:
                    bug = await self.store.update_ticket(bug.id, agent_name=owner[0], agent_role=owner[1])
                actor = owner if (owner and owner[0]) else ("Engineer", "backend")
                await self._transition(bug, TicketStatus.IN_PROGRESS, actor=actor,
                                       reason="Fixing the QA finding", kind=TicketEventKind.TRANSITIONED)

    async def _blocked_story_owner(self, bug: Ticket) -> tuple[str, str] | None:
        """(name, role) of the engineer who owns the Story this bug BLOCKS — the person who fixes it."""
        for link in await self.store.list_ticket_links(bug.id):
            if link.from_ticket == bug.id and str(link.link_type) == TicketLinkType.BLOCKS:
                story = await self.store.get_ticket(link.to_ticket)
                if story is not None and story.agent_name:
                    return (story.agent_name, story.agent_role or "backend")
        return None

    async def on_qa(
        self, mission: Mission, run_id: str, *, passed: bool, reason: str, evidence: dict | None = None,
        actor: tuple[str, str] | None = None,
    ) -> None:
        """QA verdict: PASS moves Stories to In Review (+ evidence); FAIL opens a Bug that blocks the
        Story, reopens the Story, and increments its reopen count. ``actor`` names the QA engineer who
        ran it (so the board shows WHO tested, not a generic 'QA')."""
        if not await self.enabled(mission.workspace_id):
            return
        qa = actor or ("QA", "qa")
        with contextlib.suppress(Exception):
            if passed:
                await self._on_qa_pass(mission, run_id, reason, evidence or {}, qa)
            else:
                await self._on_qa_fail(mission, run_id, reason, evidence or {}, qa)

    async def on_qa_partial(
        self, mission: Mission, run_id: str, *, reason: str,
        failing_criteria: list[str] | None = None, evidence: dict | None = None,
        qa_actor: tuple[str, str] | None = None,
    ) -> None:
        """QA PARTIAL: core criteria pass, a minor portion is deferred. Move the Stories FORWARD (In
        Review, not reopened) and file a PM-triaged, engineer-assigned FOLLOW-UP bug for the gap (a
        known-issue that rides to backlog, never blocks ship)."""
        if not await self.enabled(mission.workspace_id):
            return
        with contextlib.suppress(Exception):
            await self._on_qa_partial(mission, run_id, reason, failing_criteria or [],
                                      evidence or {}, qa_actor or ("QA", "qa"))

    async def on_review_changes(
        self, mission: Mission, reason: str, actor: tuple[str, str] | None = None,
    ) -> None:
        """Review requested changes → Stories back to In Progress with the reviewer's findings.
        ``actor`` names the reviewer (CTO) who asked for the changes."""
        if not await self.enabled(mission.workspace_id):
            return
        who = actor or ("CTO", "cto")
        with contextlib.suppress(Exception):
            for story in await self._stories(mission.id):
                if str(story.status) != TicketStatus.DONE:
                    await self._transition(story, TicketStatus.IN_PROGRESS, actor=who,
                                           reason=reason, kind=TicketEventKind.TRANSITIONED)

    async def on_reviewed(
        self, mission: Mission, *, approved: bool, reason: str = "", actor: tuple[str, str] | None = None,
    ) -> None:
        """Record a code review on every in-flight Story — WHO reviewed it and the outcome — as an
        activity comment, so the ticket shows 'Reviewed & approved by <name>' (the reviewer is the CTO)."""
        if not await self.enabled(mission.workspace_id):
            return
        who = actor or ("CTO", "cto")
        verdict = "approved" if approved else "requested changes"
        with contextlib.suppress(Exception):
            for story in await self._stories(mission.id):
                if str(story.status) == TicketStatus.DONE:
                    continue
                body = {"reason": f"Reviewed & {verdict}" + (f": {reason}" if reason else ""),
                        "review": verdict}
                if approved:
                    # Code review approved → QA verification next (build → review → QA → ship).
                    await self._transition(story, TicketStatus.QA, actor=who, reason=body["reason"],
                                           kind=TicketEventKind.TRANSITIONED, body=body)
                else:
                    await self._add_event(story, TicketEventKind.COMMENTED, actor=who, body=body)

    async def on_shipped(self, mission: Mission) -> None:
        """Mission shipped → all open Stories + the Epic to Done with a ship comment."""
        if not await self.enabled(mission.workspace_id):
            return
        with contextlib.suppress(Exception):
            await self._on_shipped(mission)

    async def on_change_request(self, mission: Mission, request: str) -> None:
        """Change request after ship → reopen the Epic and its Stories, quoting the request."""
        if not await self.enabled(mission.workspace_id):
            return
        with contextlib.suppress(Exception):
            epic = await self.store.find_ticket(mission_id=mission.id, kind=TicketKind.EPIC)
            reason = f"Change request: {request.strip()[:500]}"
            for t in ([epic] if epic else []) + await self._stories(mission.id):
                if t is None:
                    continue
                await self._reopen(t, actor=("CEO", "ceo"), reason=reason)

    async def on_cancelled(self, mission: Mission, *, reason: str = "run force-stopped") -> None:
        """Run force-stopped (mission → stopped) → Epic + open Stories move to Blocked. A stopped run
        is paused work needing attention, not done — so it belongs in Blocked, not In Progress."""
        if not await self.enabled(mission.workspace_id):
            return
        with contextlib.suppress(Exception):
            epic = await self.store.find_ticket(mission_id=mission.id, kind=TicketKind.EPIC)
            for t in ([epic] if epic else []) + await self._stories(mission.id):
                if t is None or str(t.status) in (TicketStatus.DONE, TicketStatus.BLOCKED):
                    continue
                await self._transition(t, TicketStatus.BLOCKED, actor=("system", "system"),
                                       reason=reason, kind=TicketEventKind.TRANSITIONED)

    async def on_run_started(self, mission: Mission) -> None:
        """A (re)run began → lift a settled Epic (Blocked from a prior stop, or Done being reworked)
        back to In Progress, so a retried mission reads as active instead of stuck/finished."""
        if not await self.enabled(mission.workspace_id):
            return
        with contextlib.suppress(Exception):
            epic = await self.store.find_ticket(mission_id=mission.id, kind=TicketKind.EPIC)
            if epic is not None and str(epic.status) in (TicketStatus.BLOCKED, TicketStatus.DONE):
                await self._transition(epic, TicketStatus.IN_PROGRESS, actor=("PM", "pm"),
                                       reason="a new run started", kind=TicketEventKind.TRANSITIONED)

    async def backfill(self, workspace_id: str) -> int:
        """Reconcile when the board is (re-)enabled: create an Epic for every existing mission that
        lacks one (so the board is never mysteriously empty, plan 05 §3), AND correct any existing
        Epic whose status has drifted from the mission's settled reality — a shipped mission's Epic
        must be Done, a stopped/blocked one Blocked. Best-effort; returns how many Epics were created."""
        created = 0
        with contextlib.suppress(Exception):
            for mission in await self.store.list_missions(workspace_id):
                existing = await self.store.find_ticket(mission_id=mission.id, kind=TicketKind.EPIC)
                if existing is None:
                    made = await self._ensure_epic(
                        mission, None, status=self._epic_status_for_mission(mission))
                    if made is not None:
                        created += 1
                else:
                    await self._reconcile_epic(existing, mission)
        return created

    # ---- internals --------------------------------------------------------------
    def _epic_status_for_mission(self, mission: Mission) -> TicketStatus:
        """Map a mission's stage to the Epic status the board should show (used by backfill/reconcile,
        NOT the live pipeline — a running mission's Epic is driven by its events)."""
        stage = str(mission.stage)
        if stage == "shipped":
            return TicketStatus.DONE
        if stage == "stopped" or mission.is_blocked:
            return TicketStatus.BLOCKED
        return {
            "backlog": TicketStatus.TODO, "spec": TicketStatus.TODO,
            "building": TicketStatus.IN_PROGRESS, "qa": TicketStatus.QA,
            "review": TicketStatus.IN_REVIEW,
        }.get(stage, TicketStatus.IN_PROGRESS)

    async def _reconcile_epic(self, epic: Ticket, mission: Mission) -> None:
        """Correct an existing Epic toward a SETTLED mission state (Done / Blocked) only — never
        clobber a legitimately mid-pipeline Epic (in_progress/qa/in_review) the live consumer owns."""
        target = self._epic_status_for_mission(mission)
        if target in (TicketStatus.DONE, TicketStatus.BLOCKED) and str(epic.status) != target:
            await self._transition(epic, target, actor=("system", "system"),
                                   reason=f"reconciled to mission stage: {mission.stage}",
                                   kind=TicketEventKind.TRANSITIONED)

    async def _ensure_epic(
        self, mission: Mission, criteria: list | None, *, status: TicketStatus = TicketStatus.IN_PROGRESS,
    ) -> Ticket:
        existing = await self.store.find_ticket(mission_id=mission.id, kind=TicketKind.EPIC)
        if existing is not None:
            return existing
        acceptance = [getattr(c, "criterion", str(c)) for c in (criteria or [])]
        epic = await self._new_ticket(
            mission, TicketKind.EPIC, title=mission.title,
            description={"goal": (mission.summary or mission.requirements or "").strip(),
                         "acceptance": acceptance},
            status=status, labels=["shipwright", f"mission:{mission.key}"],
        )
        await self._add_event(epic, TicketEventKind.CREATED, actor=("PM", "pm"),
                              body={"reason": f"Mission {mission.key} accepted"})
        return epic

    async def _sync_build(self, mission: Mission, facts: dict) -> None:
        epic = await self._ensure_epic(mission, None)
        slices = facts.get("subtasks") or []
        branch = facts.get("branch")
        for sl in slices:
            story = await self.store.find_ticket(
                mission_id=mission.id, kind=TicketKind.STORY, subtask_id=sl.get("subtask_id"))
            actor = (sl.get("agent_name") or "Agent", sl.get("role") or "backend")
            if story is None:
                story = await self._new_ticket(
                    mission, TicketKind.STORY, title=sl.get("title") or mission.title,
                    parent_id=epic.id, subtask_id=sl.get("subtask_id"),
                    description={"goal": sl.get("title"), "dod": _DOD,
                                 "owned_paths": sl.get("files") or []},
                    status=TicketStatus.IN_PROGRESS,
                    agent_name=actor[0], agent_role=actor[1],
                    labels=[f"agent:{actor[1]}-{_slug(actor[0])}"],
                )
                await self._add_event(story, TicketEventKind.CREATED, actor=actor,
                                      body={"reason": "Subtask planned"})
            files = sl.get("files") or []
            comment = (f"Built {len(files)} file(s): {', '.join(files[:12])}"
                       + (" …" if len(files) > 12 else "")
                       + (f" · committed to {branch}" if branch else ""))
            # Build complete → CODE REVIEW next (pipeline order is build → review → QA → ship).
            await self._transition(story, TicketStatus.IN_REVIEW, actor=actor, reason=comment,
                                   kind=TicketEventKind.TRANSITIONED,
                                   body={"files": files, "branch": branch})

    async def _on_qa_pass(self, mission: Mission, run_id: str, reason: str, evidence: dict,
                          qa: tuple[str, str] = ("QA", "qa")) -> None:
        shots = list((evidence or {}).get("screenshots") or [])
        for story in await self._stories(mission.id):
            if str(story.status) in (TicketStatus.DONE,):
                continue
            # QA is the FINAL gate (code already reviewed) → keep the story in QA, verified & ready to
            # ship; the ship step moves it to Done. Don't send it backward to In Review.
            if str(story.status) != TicketStatus.QA:
                await self._transition(story, TicketStatus.QA, actor=qa,
                                       reason=reason or "QA verified", kind=TicketEventKind.TRANSITIONED)
            await self._add_event(story, TicketEventKind.COMMENTED, actor=qa,
                                  body={"reason": "QA passed ✓ — ready to ship"
                                        + (f": {reason}" if reason else "")})
            if shots:
                await self._add_event(story, TicketEventKind.EVIDENCE_ATTACHED, actor=qa,
                                      body={"artifact_ids": shots,
                                            "summary": (evidence or {}).get("summary", "")})
        # The QA findings are fixed (the build now passes) → resolve the open BLOCKER bugs so they don't
        # linger. QA verified the fix, so QA closes them. Follow-up bugs (known issues) are left open.
        for bug in await self._blocker_bugs(mission.id):
            await self._transition(bug, TicketStatus.DONE, actor=qa,
                                   reason="Fixed — QA now passes", kind=TicketEventKind.TRANSITIONED)

    async def _on_qa_fail(self, mission: Mission, run_id: str, reason: str, evidence: dict,
                          qa: tuple[str, str] = ("QA", "qa")) -> None:
        epic = await self.store.find_ticket(mission_id=mission.id, kind=TicketKind.EPIC)
        # Reopen the FAILING story (mapped from the failing criteria), not blindly the first one.
        target = await self._target_story(mission, list((evidence or {}).get("failing_criteria", [])))
        bug = await self.store.find_ticket(mission_id=mission.id, kind=TicketKind.BUG, run_id=run_id)
        if bug is None:
            bug = await self._new_ticket(
                mission, TicketKind.BUG, title=f"QA failed: {(reason or 'acceptance not met')[:80]}",
                parent_id=epic.id if epic else None, run_id=run_id,
                description={"goal": "Fix the QA finding", "failure": reason,
                             "evidence": (evidence or {}).get("checks", [])},
                status=TicketStatus.TODO, agent_name=qa[0], agent_role=qa[1], priority="P2",
            )
            await self._add_event(bug, TicketEventKind.CREATED, actor=qa,
                                  body={"reason": reason, "run_id": run_id,
                                        "artifact_ids": list((evidence or {}).get("screenshots") or [])})
            if target is not None:
                await self.store.add_ticket_link(TicketLink(
                    id=new_ulid(), workspace_id=mission.workspace_id,
                    from_ticket=bug.id, to_ticket=target.id, link_type=TicketLinkType.BLOCKS))
                await self._add_event(target, TicketEventKind.LINKED, actor=qa,
                                      body={"blocked_by": bug.key, "blocked_by_id": bug.id})
        if target is not None:
            await self._reopen(target, actor=qa, reason=reason or "QA failed")

    async def _on_qa_partial(self, mission: Mission, run_id: str, reason: str,
                             failing_criteria: list[str], evidence: dict, qa: tuple[str, str]) -> None:
        # FORWARD: the Stories move to In Review (they are NOT reopened) — core criteria passed.
        shots = list((evidence or {}).get("screenshots") or [])
        for story in await self._stories(mission.id):
            if str(story.status) == TicketStatus.DONE:
                continue
            await self._transition(story, TicketStatus.IN_REVIEW, actor=qa,
                                   reason=reason or "QA partial — core criteria pass",
                                   kind=TicketEventKind.TRANSITIONED)
            if shots:
                await self._add_event(story, TicketEventKind.EVIDENCE_ATTACHED, actor=qa,
                                      body={"artifact_ids": shots, "summary": (evidence or {}).get("summary", "")})
        # FOLLOW-UP bug: TRIAGED by the PM (separation of duty — QA reports, PM assigns), owned by the
        # engineer of the failing story, P3, labelled follow-up/known-issue, linked RELATES (non-blocking)
        # so the story keeps moving. This bug is backlog: it never blocks ship and is never force-closed.
        epic = await self.store.find_ticket(mission_id=mission.id, kind=TicketKind.EPIC)
        eng = await self._owning_engineer(mission, failing_criteria)
        pm = ("PM", "pm")
        bug = await self._new_ticket(
            mission, TicketKind.BUG, title=f"Follow-up: {(reason or 'minor gap')[:80]}",
            parent_id=epic.id if epic else None, run_id=run_id,
            description={"goal": "Complete the deferred portion", "failure": reason,
                         "evidence": (evidence or {}).get("checks", []), "criteria": failing_criteria},
            status=TicketStatus.TODO, agent_name=eng[0], agent_role=eng[1], priority="P3",
            labels=["follow-up", "known-issue", f"role:{eng[1]}"])
        await self._add_event(bug, TicketEventKind.CREATED, actor=pm,
                              body={"reason": reason, "run_id": run_id, "assignee": eng[0]})
        await self._add_event(bug, TicketEventKind.COMMENTED, actor=pm,
                              body={"reason": f"Triaged by PM: forwarded with a follow-up assigned to {eng[0]}"})
        target = await self._target_story(mission, failing_criteria)
        if target is not None:
            with contextlib.suppress(Exception):
                await self.store.add_ticket_link(TicketLink(
                    id=new_ulid(), workspace_id=mission.workspace_id,
                    from_ticket=bug.id, to_ticket=target.id, link_type=TicketLinkType.RELATES))

    async def _target_story(self, mission: Mission, failing_criteria: list[str]):
        """The Story a QA finding maps to: match a failing criterion id/text against the story's
        acceptance list or title; else the first non-done story (behaviour-preserving fallback)."""
        stories = await self._stories(mission.id)
        for s in stories:
            acc = " ".join(str(x) for x in (s.description or {}).get("acceptance", []))
            if any(fc and (fc in acc or fc in (s.title or "")) for fc in (failing_criteria or [])):
                return s
        return next((s for s in stories if str(s.status) != TicketStatus.DONE), None)

    async def _owning_engineer(self, mission: Mission, failing_criteria: list[str]) -> tuple[str, str]:
        """(name, role) of the engineer who owns the failing story — the person the follow-up goes to."""
        s = await self._target_story(mission, failing_criteria)
        if s is not None and s.agent_name:
            return (s.agent_name, s.agent_role or "backend")
        return ("Engineer", "backend")

    async def _on_shipped(self, mission: Mission) -> None:
        epic = await self.store.find_ticket(mission_id=mission.id, kind=TicketKind.EPIC)
        for story in await self._stories(mission.id):
            if str(story.status) != TicketStatus.DONE:
                await self._transition(story, TicketStatus.DONE, actor=("DevOps", "devops"),
                                       reason="Mission shipped", kind=TicketEventKind.SHIPPED)
        for bug in await self._blocker_bugs(mission.id):  # close BLOCKER bugs (never a known-issue follow-up)
            await self._transition(bug, TicketStatus.DONE, actor=("DevOps", "devops"),
                                   reason="Resolved — mission shipped", kind=TicketEventKind.SHIPPED)
        # Follow-up (known-issue) bugs SURVIVE the ship as backlog — never force-closed.
        for fu in [b for b in await self._open_bugs(mission.id) if "follow-up" in (b.labels or [])]:
            await self._add_event(fu, TicketEventKind.COMMENTED, actor=("DevOps", "devops"),
                                  body={"reason": "Carried to backlog — mission shipped with a known gap"})
        if epic is not None and str(epic.status) != TicketStatus.DONE:
            await self._transition(epic, TicketStatus.DONE, actor=("DevOps", "devops"),
                                   reason="Mission shipped — pipeline complete",
                                   kind=TicketEventKind.SHIPPED)

    async def _stories(self, mission_id: str) -> list[Ticket]:
        return [t for t in await self.store.list_tickets(mission_id=mission_id)
                if str(t.kind) == TicketKind.STORY]

    async def _open_bugs(self, mission_id: str) -> list[Ticket]:
        """QA-filed bugs that aren't resolved yet (a bug is 'open' until it's Done)."""
        return [t for t in await self.store.list_tickets(mission_id=mission_id)
                if str(t.kind) == TicketKind.BUG and str(t.status) != TicketStatus.DONE]

    async def _blocker_bugs(self, mission_id: str) -> list[Ticket]:
        """Open bugs that BLOCK shipping — i.e. NOT carried-forward follow-ups. Only these auto-close on
        QA-pass / ship; a 'follow-up' bug is a known-issue backlog item and must survive (a partial
        gap can never both ship AND silently lose its tracking ticket)."""
        return [b for b in await self._open_bugs(mission_id) if "follow-up" not in (b.labels or [])]

    async def _reopen(self, ticket: Ticket, *, actor: tuple[str, str], reason: str) -> Ticket:
        updated = await self.store.update_ticket(
            ticket.id, status=TicketStatus.REOPENED.value,
            reopen_count=(ticket.reopen_count or 0) + 1)
        await self._add_event(updated, TicketEventKind.REOPENED, actor=actor,
                              body={"reason": reason, "reopen_count": updated.reopen_count},
                              from_status=str(ticket.status), to_status=TicketStatus.REOPENED)
        return updated

    async def _transition(
        self, ticket: Ticket, to_status: TicketStatus, *, actor: tuple[str, str], reason: str,
        kind: TicketEventKind, body: dict | None = None,
    ) -> Ticket:
        from_status = str(ticket.status)
        updated = await self.store.update_ticket(ticket.id, status=to_status.value)
        await self._add_event(updated, kind, actor=actor,
                              body={"reason": reason, **(body or {})},
                              from_status=from_status, to_status=to_status)
        return updated

    async def _new_ticket(self, mission: Mission, kind: TicketKind, **fields) -> Ticket:
        key = await self.store.next_ticket_key()
        now = _now()
        ticket = Ticket(
            id=new_ulid(), workspace_id=mission.workspace_id, key=key, kind=kind,
            mission_id=mission.id, created_at=now, updated_at=now, **fields)
        return await self.store.add_ticket(ticket)

    async def _add_event(
        self, ticket: Ticket, kind: TicketEventKind, *, actor: tuple[str, str], body: dict,
        from_status: str | None = None, to_status: TicketStatus | str | None = None,
    ) -> None:
        ev = await self.store.add_ticket_event(TicketEvent(
            id=new_ulid(), workspace_id=ticket.workspace_id, ticket_id=ticket.id, kind=kind,
            from_status=str(from_status) if from_status else None,
            to_status=str(to_status) if to_status else None,
            actor_name=actor[0], actor_role=actor[1], body=body, created_at=_now()))
        # Notify the mirror sink (Jira). Best-effort — an outbox failure must never disturb the board.
        if self._sink is not None:
            with contextlib.suppress(Exception):
                await self._sink(ticket, ev)

    async def _record_sync_error(self, ticket: Ticket | None, detail: str) -> None:
        """Surface a consumer failure as a ticket event when we have a ticket; otherwise it is lost
        to the pipeline by design (Rule 0 — never fail the run). Best-effort, never raises."""
        if ticket is None:
            return
        with contextlib.suppress(Exception):
            await self._add_event(ticket, TicketEventKind.SYNC_ERROR, actor=("system", "system"),
                                  body={"error": detail})


def _slug(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in (name or "")).strip("-")[:24] or "agent"
