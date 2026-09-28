"""Execute a WhatsApp control-plane *action* against the orchestrator's engine + store.

WHY this is a thin, defensive executor:
:mod:`app.wa_session` turns free text into a small ``action`` dict; this module is the ONLY place
that touches side-effecting engine/store methods. Two rules govern everything here:

  * **Never raise.** A WhatsApp reply must always be produced — an inbound webhook handler cannot be
    allowed to 500 because of a malformed action or a store hiccup. The whole body is wrapped and any
    unexpected error becomes a safe, generic reply (failure isolation).
  * **Duck-typed / getattr-guarded engine calls.** The engine surface evolves; rather than importing
    and binding to a concrete class, we look methods up by name and degrade gracefully ("that isn't
    wired up yet") when one is absent. This keeps the control plane decoupled and trivially fakeable
    in tests.

The method names assumed here match the real :class:`app.engine.Engine` where they exist
(``cancel_run``, ``retry_run``, ``submit_clarification``, ``resolve_blocker``) plus one small protocol
method — ``start_mission_from_text(brief, *, actor, workspace_id)`` — that the inbound handler is
expected to provide (with ``create_and_run`` as a fallback name).
"""
from __future__ import annotations

from typing import Any

from foundry_core.enums import ApprovalDecision, BlockerKind

from .seed import DEMO_WS

_GENERIC_ERROR = "Sorry, something went wrong handling that."

# Board columns in reading order → friendly labels, for the WhatsApp ticket breakdown.
_TICKET_COLUMNS: list[tuple[str, str]] = [
    ("todo", "To Do"), ("reopened", "Reopened"), ("in_progress", "In Progress"),
    ("in_review", "In Review"), ("qa", "QA"), ("blocked", "Blocked"), ("done", "Done"),
]


def _ticket_breakdown(tickets: list[Any]) -> str:
    """A compact 'how many To Do / In Progress / QA / Done' string, empty when there are no tickets."""
    counts: dict[str, int] = {}
    for t in tickets:
        status = getattr(getattr(t, "status", None), "value", None) or str(getattr(t, "status", ""))
        counts[status] = counts.get(status, 0) + 1
    parts = [f"{counts[val]} {label}" for val, label in _TICKET_COLUMNS if counts.get(val)]
    return " · ".join(parts)


async def _safe_tickets(store: Any, *, mission_id: str | None = None, workspace_id: str) -> list[Any]:
    """Best-effort ticket fetch — a store without ``list_tickets`` (or a hiccup) yields ``[]``, never raises."""
    fn = getattr(store, "list_tickets", None)
    if fn is None:
        return []
    try:
        if mission_id is not None:
            return list(await fn(mission_id=mission_id, workspace_id=workspace_id))
        return list(await fn(workspace_id=workspace_id))
    except Exception:  # noqa: BLE001 — status is read-only + failure-isolated
        return []


async def execute(
    action: dict, *, store: Any, engine: Any, actor: str, workspace_id: str
) -> str:
    """Run one action dict and return a short, WhatsApp-friendly reply. NEVER raises."""
    try:
        do = (action or {}).get("do")
        if do == "start":
            return await _do_start(action, engine=engine, actor=actor, workspace_id=workspace_id)
        if do == "cancel":
            return await _do_cancel(action, store=store, engine=engine, actor=actor)
        if do == "retry":
            return await _do_retry(action, store=store, engine=engine)
        if do == "answer":
            return await _do_answer(action, engine=engine, actor=actor)
        if do in ("approve", "reject"):
            return await _do_decision(
                action, store=store, engine=engine, actor=actor, workspace_id=workspace_id
            )
        if do == "push_decision":
            return await _do_push_decision(action, engine=engine, actor=actor)
        if do == "status":
            return await _do_status(store=store, workspace_id=workspace_id)
        if do == "missions":
            return await _do_missions(store=store, workspace_id=workspace_id)
        if do == "mission":
            return await _do_mission(action, store=store)
        if do == "teams":
            return await _do_teams(store=store, workspace_id=workspace_id)
        if do == "team":
            return await _do_team(action, store=store, workspace_id=workspace_id)
        if do == "agents":
            return await _do_agents(store=store, workspace_id=workspace_id)
        if do == "agent":
            return await _do_agent(action, store=store, workspace_id=workspace_id)
        if do == "skills":
            return await _do_skills(store=store, workspace_id=workspace_id)
        if do == "models":
            return await _do_models(store=store, workspace_id=workspace_id)
        return "Not sure what to do with that. Send 'help' for options."
    except Exception:  # noqa: BLE001 — failure isolation: a control action must never crash the caller.
        return _GENERIC_ERROR


# --- individual action handlers -------------------------------------------------


async def _do_start(action: dict, *, engine: Any, actor: str, workspace_id: str) -> str:
    brief = (action.get("brief") or "").strip()
    if not brief:
        return "Tell me what to build first, e.g. 'start build a todo app'."
    starter = getattr(engine, "start_mission_from_text", None) or getattr(engine, "create_and_run", None)
    if starter is None:
        return "Starting missions from chat isn't wired up yet — kick it off from the dashboard."
    mission = await starter(brief, actor=actor, workspace_id=workspace_id)
    key = _key_of(mission) or "your mission"
    return f"Started {key} ✅ — I'll message you when I need approvals or have questions."


async def _do_cancel(action: dict, *, store: Any, engine: Any, actor: str) -> str:
    key = action.get("mission_key")
    if not key:
        return "Which mission should I stop? Send e.g. 'cancel M-152'."
    mission = await store.get_mission(key)
    if mission is None:
        return f"Couldn't find {key}."
    canceller = getattr(engine, "cancel_run", None)
    if canceller is None:
        return f"Can't stop {key} right now."
    await canceller(mission, actor=actor)
    label = _key_of(mission) or key
    return f"Stopped {label}. Progress is saved — reply 'retry {label}' to resume."


async def _do_retry(action: dict, *, store: Any, engine: Any) -> str:
    key = action.get("mission_key")
    if not key:
        return "Which mission should I retry? Send e.g. 'retry M-152'."
    mission = await store.get_mission(key)
    if mission is None:
        return f"Couldn't find {key}."
    retrier = getattr(engine, "retry_run", None) or getattr(engine, "retry", None)
    if retrier is None:
        return f"Can't retry {key} right now."
    await retrier(mission)
    return f"Retrying {_key_of(mission) or key}…"


async def _do_answer(action: dict, *, engine: Any, actor: str) -> str:
    answer = (action.get("answer") or "").strip()
    blocker_id = action.get("blocker_id")
    if not answer:
        return "Send your answer as text and I'll pass it along."
    if not blocker_id:
        return "I don't have an open question for you right now."
    submit = getattr(engine, "submit_clarification", None)
    if submit is None:
        return "Can't record your answer right now."
    # The engine expects a list of ``{"question"?, "answer"}`` dicts; it filters on a non-empty answer.
    await submit(blocker_id, [{"answer": answer}], actor=actor)
    label = action.get("mission_key") or "the team"
    return f"Got it — passed your answer to {label}."


async def _do_decision(
    action: dict, *, store: Any, engine: Any, actor: str, workspace_id: str
) -> str:
    approve = action.get("do") == "approve"
    key = action.get("mission_key")
    if not key:
        verb = "approve" if approve else "reject"
        return f"Which mission? Send e.g. '{verb} M-152'."
    mission = await store.get_mission(key)
    if mission is None:
        return f"Couldn't find {key}."
    label = _key_of(mission) or key
    pending = [
        b
        for b in await store.list_blockers(workspace_id)
        if getattr(b, "mission_id", None) == getattr(mission, "id", None) and _is_approval(b)
    ]
    if not pending:
        return f"No pending approval on {label}."
    resolver = getattr(engine, "resolve_blocker", None)
    if resolver is None:
        return "Can't record that decision right now."
    decision = ApprovalDecision.APPROVE if approve else ApprovalDecision.REJECT
    # Resolve the most recent open approval (there is normally exactly one).
    await resolver(pending[-1].id, decision, actor=actor, note=f"via {actor}")
    return f"{'✅ Approved' if approve else '🛑 Rejected'} {label}."


async def _do_push_decision(action: dict, *, engine: Any, actor: str) -> str:
    """Resolve a push-rejected merge gate the way the user chose in chat: 'force push' (overwrite the
    branch — an explicit, human-authorized force) or 'new branch <name>' (push a fresh branch, safe)."""
    blocker_id = action.get("blocker_id")
    if not blocker_id:
        return "I don't have an open push gate for you right now."
    resolver = getattr(engine, "resolve_blocker", None)
    if resolver is None:
        return "Can't record that decision right now."
    key = action.get("mission_key") or "the mission"
    force = action.get("mode") == "force"
    branch = (action.get("branch") or "").strip()
    if not force and not branch:
        # No name given → generate a fresh, non-colliding branch so we never touch the base branch.
        branch = f"shipwright/{str(action.get('mission_key') or 'mission').lower()}"
    await resolver(blocker_id, ApprovalDecision.APPROVE, actor=actor, note=f"via {actor}",
                   branch=(branch or None), force=force)
    if force:
        return f"⚠️ Authorized a force-push for {key} — overwriting the branch."
    return f"✅ Pushing {key} to a new branch '{branch}' and opening a PR."


async def _do_status(*, store: Any, workspace_id: str) -> str:
    missions = await _safe_list_missions(store, workspace_id)
    if missions is None:
        return "Can't fetch status right now."
    if not missions:
        return "No missions yet. Send 'start <brief>' to kick one off."
    counts: dict[str, int] = {}
    for mission in missions:
        stage = _stage_of(mission)
        counts[stage] = counts.get(stage, 0) + 1
    breakdown = ", ".join(f"{n} {stage}" for stage, n in sorted(counts.items()))
    tickets = await _safe_tickets(store, workspace_id=workspace_id)
    bd = _ticket_breakdown(tickets)
    ticket_line = f" Tickets: {bd}." if bd else ""
    return f"{len(missions)} mission(s): {breakdown}.{ticket_line}"


async def _do_missions(*, store: Any, workspace_id: str) -> str:
    missions = await _safe_list_missions(store, workspace_id)
    if missions is None:
        return "Can't list missions right now."
    if not missions:
        return "No missions yet. Send 'start <brief>' to kick one off."
    lines = [_mission_line(mission) for mission in missions[:10]]
    more = "" if len(missions) <= 10 else f"\n…and {len(missions) - 10} more."
    return "Your missions:\n" + "\n".join(lines) + more


async def _do_mission(action: dict, *, store: Any) -> str:
    key = action.get("mission_key")
    if not key:
        return "Which mission? Send e.g. 'mission M-152'."
    mission = await store.get_mission(key)
    if mission is None:
        return f"Couldn't find {key}."
    label = _key_of(mission) or key
    title = (getattr(mission, "title", "") or "").strip()
    progress = getattr(mission, "progress", None)
    tail = f" — {progress}%" if isinstance(progress, int) else ""
    base = f"{label} — {_stage_of(mission)}{tail}{(' — ' + title) if title else ''}"
    tickets = await _safe_tickets(store, mission_id=getattr(mission, "id", None),
                                  workspace_id=getattr(mission, "workspace_id", "") or DEMO_WS)
    bd = _ticket_breakdown(tickets)
    return base + (f"\nTickets: {bd}" if bd else "")


# --- read-only org views (Tier 1) -----------------------------------------------


async def _do_teams(*, store: Any, workspace_id: str) -> str:
    teams = await _safe_list(store, "list_teams", workspace_id)
    if teams is None:
        return "Can't list teams right now."
    if not teams:
        return "No teams yet — create one from the dashboard."
    lines = [f"• {getattr(t, 'name', '?')} ({len(getattr(t, 'members', []) or [])} member(s))"
             for t in teams[:15]]
    return "Your teams:\n" + "\n".join(lines)


async def _do_team(action: dict, *, store: Any, workspace_id: str) -> str:
    name = (action.get("name") or "").strip()
    if not name:
        return "Which team? Send e.g. 'team Core'."
    teams = await _safe_list(store, "list_teams", workspace_id)
    if teams is None:
        return "Can't look that up right now."
    team = _find_by_name(teams, name)
    if team is None:
        return f"Couldn't find a team called '{name}'. Send 'teams' to list them."
    agents = await _safe_list(store, "list_agents", workspace_id) or []
    by_id = {getattr(a, "id", None): a for a in agents}
    members = getattr(team, "members", []) or []
    header = f"{getattr(team, 'name', name)}"
    desc = (getattr(team, "description", "") or "").strip()
    lines = [header + (f" — {desc}" if desc else "")]
    if members:
        lines.append("Members:")
        for m in members[:20]:
            a = by_id.get(m.get("agentId") if isinstance(m, dict) else None)
            who = getattr(a, "name", None) or (m.get("agentId") if isinstance(m, dict) else "?")
            role = _role_of(a) if a is not None else ""
            acc = " · accountable" if isinstance(m, dict) and m.get("accountable") else ""
            lines.append(f"• {who}{f' ({role})' if role else ''}{acc}")
    else:
        lines.append("(no members yet)")
    return "\n".join(lines)


async def _do_agents(*, store: Any, workspace_id: str) -> str:
    agents = await _safe_list(store, "list_agents", workspace_id)
    if agents is None:
        return "Can't list agents right now."
    if not agents:
        return "No agents yet — add some from the dashboard."
    lines = [f"• {getattr(a, 'name', '?')} ({_role_of(a)}) · {_status_of(a)}" for a in agents[:20]]
    return "Your agents:\n" + "\n".join(lines)


async def _do_agent(action: dict, *, store: Any, workspace_id: str) -> str:
    name = (action.get("name") or "").strip()
    if not name:
        return "Which agent? Send e.g. 'agent Ada'."
    agents = await _safe_list(store, "list_agents", workspace_id)
    if agents is None:
        return "Can't look that up right now."
    agent = _find_by_name(agents, name)
    if agent is None:
        return f"Couldn't find an agent called '{name}'. Send 'agents' to list them."
    models = getattr(agent, "models", []) or []
    model = ", ".join(models) if models else (getattr(agent, "model_binding", "") or "—")
    skills = getattr(agent, "skills", []) or []
    skills_txt = ", ".join(skills[:12]) if skills else "—"
    return (f"{getattr(agent, 'name', name)} — {_role_of(agent)} · {_status_of(agent)}\n"
            f"Model: {model}\nSkills: {skills_txt}")


async def _do_skills(*, store: Any, workspace_id: str) -> str:
    skills = await _safe_list(store, "list_skills", workspace_id)
    if skills is None:
        return "Can't list skills right now."
    if not skills:
        return "No skills yet."
    lines = []
    for s in skills[:15]:
        cat = getattr(getattr(s, "category", None), "value", None) or getattr(s, "category", "")
        succ, fail = int(getattr(s, "successes", 0) or 0), int(getattr(s, "fails", 0) or 0)
        eff = f" · {round(100 * succ / (succ + fail))}% eff" if (succ + fail) else ""
        lines.append(f"• {getattr(s, 'name', '?')} ({cat}){eff}")
    more = "" if len(skills) <= 15 else f"\n…and {len(skills) - 15} more."
    return f"Skill library ({len(skills)}):\n" + "\n".join(lines) + more


async def _do_models(*, store: Any, workspace_id: str) -> str:
    conns = await _safe_list(store, "list_model_connections", workspace_id)
    if conns is None:
        return "Can't list models right now."
    if not conns:
        return "No model connections yet — add one under Models."
    lines = []
    for c in conns[:15]:
        prov = getattr(getattr(c, "provider", None), "value", None) or getattr(c, "provider", "?")
        models = getattr(c, "models", []) or []
        mtxt = f" ({', '.join(models[:3])})" if models else ""
        status = getattr(getattr(c, "status", None), "value", None) or getattr(c, "status", "")
        primary = " · primary" if getattr(c, "is_primary", False) else ""
        lines.append(f"• {prov}{mtxt} · {status}{primary}")
    return "Model connections:\n" + "\n".join(lines)


# --- small duck-typed helpers ---------------------------------------------------


def _mission_line(mission: Any) -> str:
    title = (getattr(mission, "title", "") or "").strip()
    line = f"{_key_of(mission) or '?'} — {_stage_of(mission)}"
    return f"{line} — {title}" if title else line


def _key_of(mission: Any) -> str | None:
    key = getattr(mission, "key", None)
    return str(key) if key else None


def _stage_of(mission: Any) -> str:
    stage = getattr(mission, "stage", None)
    if stage is None:
        return "unknown"
    return getattr(stage, "value", None) or str(stage)


def _is_approval(blocker: Any) -> bool:
    kind = getattr(blocker, "kind", None)
    return kind in (BlockerKind.APPROVAL, "approval") or getattr(kind, "value", None) == "approval"


async def _safe_list_missions(store: Any, workspace_id: str) -> list[Any] | None:
    lister = getattr(store, "list_missions", None)
    if lister is None:
        return None
    return list(await lister(workspace_id))


async def _safe_list(store: Any, method: str, workspace_id: str) -> list[Any] | None:
    """Call a workspace-scoped list method by name, or None when the store doesn't support it."""
    lister = getattr(store, method, None)
    if lister is None:
        return None
    return list(await lister(workspace_id))


def _find_by_name(items: list[Any], name: str) -> Any | None:
    """Case-insensitive match on ``.name`` (exact first, then a substring), or None."""
    want = name.strip().lower()
    for it in items:
        if (getattr(it, "name", "") or "").strip().lower() == want:
            return it
    for it in items:
        if want in (getattr(it, "name", "") or "").strip().lower():
            return it
    return None


def _role_of(agent: Any) -> str:
    role = getattr(agent, "role_key", None) or getattr(agent, "role", None) or ""
    role = getattr(role, "value", None) or str(role)
    return role.replace("_", " ").title() if role else "—"


def _status_of(agent: Any) -> str:
    status = getattr(agent, "status", None)
    return getattr(status, "value", None) or str(status) if status is not None else "—"
