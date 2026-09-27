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

_GENERIC_ERROR = "Sorry, something went wrong handling that."


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
        if do == "status":
            return await _do_status(store=store, workspace_id=workspace_id)
        if do == "missions":
            return await _do_missions(store=store, workspace_id=workspace_id)
        if do == "mission":
            return await _do_mission(action, store=store)
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
    return f"{len(missions)} mission(s): {breakdown}."


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
    return f"{label} — {_stage_of(mission)}{tail}{(' — ' + title) if title else ''}"


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
