"""Conversational WhatsApp control-plane brain — intent parsing + a per-sender state machine.

WHY a state machine (and not the stateless :mod:`app.inbound` grammar)?
WhatsApp is *free text* and *stateful per person*: a user says "start a todo app", we ask them to
confirm, and their next message ("yes") only makes sense in the context of what we just asked. So a
sender carries a :class:`WaSessionState` between messages, and every message is interpreted RELATIVE
to that state.

Two safety properties are baked into the grammar here (they cannot be bypassed by wording):
  * **Destructive actions require an explicit confirm step.** ``start`` (spins up an org + spends
    tokens) and ``cancel`` (force-stops a run) NEVER act on the first message — they move the sender
    to :attr:`WaState.AWAITING_CONFIRM` and only emit an action after a follow-up "confirm"/"yes".
  * **Ambiguity resolves to help, never to a guess.** Free text we can't classify returns an
    ``unknown``/``help`` hint rather than firing an action the user didn't ask for.

This module is PURE LOGIC: no I/O, no DB, no network. :func:`parse_intent` classifies a message and
:func:`advance` decides the reply + an optional ``action`` dict that :mod:`app.wa_control` executes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from foundry_core.brand import BRAND_NAME


class WaState(StrEnum):
    """Where a sender's conversation currently sits."""

    IDLE = "idle"
    AWAITING_CONFIRM = "awaiting_confirm"   # a destructive action is staged; waiting for confirm/abort
    AWAITING_ANSWER = "awaiting_answer"     # the AI asked clarifying questions; next text is the answer
    DRAFTING_MISSION = "drafting_mission"   # user said "start" with no brief; waiting for the brief


@dataclass
class WaSessionState:
    """Per-sender conversation memory. ``context`` holds the pending draft / the blocker we're
    awaiting an answer for, e.g. ``{"pending": "start", "draft_brief": ...}`` or
    ``{"mission_key": "M-152", "blocker_id": "B1", "questions": [...]}``."""

    sender: str
    state: WaState = WaState.IDLE
    context: dict = field(default_factory=dict)


@dataclass
class Intent:
    """A classified message. ``kind`` is one of:
    start|cancel|retry|answer|approve|reject|status|missions|mission|help|confirm|abort|unknown."""

    kind: str
    args: dict = field(default_factory=dict)


# A canonical mission key: 1-6 uppercase letters, a dash, digits — e.g. ``M-152``, ``FND-142``.
_KEY_RE = re.compile(r"\b([A-Z]{1,6}-\d+)\b")

# Leading-word vocabularies. Kept small and explicit so classification is predictable, not "clever".
_START_WORDS = frozenset({"start", "build", "create"})
_CANCEL_WORDS = frozenset({"cancel", "stop", "end"})
_APPROVE_WORDS = frozenset({"approve", "approved", "yes", "ok", "okay", "lgtm", "y"})
_REJECT_WORDS = frozenset({"reject", "rejected", "no", "nope", "deny", "denied", "decline"})
_HELP_WORDS = frozenset({"help", "?", "h", "commands"})
# In AWAITING_CONFIRM only these mean "go ahead"; EVERYTHING else is treated as an abort (fail-safe:
# we never perform a destructive action on an ambiguous reply).
_CONFIRM_WORDS = frozenset({"confirm", "yes", "y", "yeah", "yep", "ok", "okay", "sure", "lgtm"})
# Phrases that back out of a staged action or an answering flow.
_ABORT_PHRASES = frozenset({"abort", "nevermind", "never mind", "cancel that", "cancel that."})
# "list <thing>" → the read-only view for that thing (Tier 1).
_LIST_TARGETS = {"teams": "teams", "agents": "agents", "skills": "skills",
                 "models": "models", "missions": "missions"}

_HELP_TEXT = (
    f"{BRAND_NAME} on WhatsApp — what I understand:\n"
    "• start <brief> — kick off a mission (I'll ask you to confirm)\n"
    "• cancel <KEY> — stop a mission (I'll ask you to confirm)\n"
    "• retry <KEY> — resume a stopped mission\n"
    "• status — how your missions are doing\n"
    "• missions — list your missions\n"
    "• mission <KEY> — details on one\n"
    "• approve <KEY> / reject <KEY> — decide an open gate\n"
    "• teams · team <name> — your teams / one team's members\n"
    "• agents · agent <name> — your agents / one agent's role, skills, model\n"
    "• skills · models — the skill library / model connections\n"
    "• (when I ask questions) just reply with your answer\n"
    "Reply 'confirm' or 'abort' whenever I ask you to confirm."
)


def extract_key(text: str) -> str | None:
    """Pull a mission key (``M-152`` / ``FND-142``) out of free text, or ``None``.

    We try the text as-typed first (keys are canonically upper-case), then fall back to an
    upper-cased scan so a user who types ``m-152`` in lower-case is still understood."""
    if not text:
        return None
    match = _KEY_RE.search(text)
    if match:
        return match.group(1)
    match = _KEY_RE.search(text.upper())
    return match.group(1) if match else None


def parse_intent(text: str, session: WaSessionState) -> Intent:
    """Classify one inbound message into an :class:`Intent`, RELATIVE to the sender's state.

    Order matters: state-sensitive branches (confirm / answer / drafting) run first, because in those
    states the same words mean different things (a bare "yes" is a confirmation, not an approval)."""
    raw = (text or "").strip()
    # Accept slash-commands (WhatsApp agents often use "/help", "/status", "/start …"): strip a single
    # leading slash from the first token so "/help" reads the same as "help".
    if raw.startswith("/") and not raw.startswith("//"):
        raw = raw[1:].lstrip()
    low = raw.lower()
    if not raw:
        return Intent("help")

    # --- state-sensitive branches -------------------------------------------------
    if session.state == WaState.AWAITING_CONFIRM:
        # Only an explicit yes proceeds; anything else aborts the staged (destructive) action.
        return Intent("confirm") if low in _CONFIRM_WORDS else Intent("abort")

    if session.state == WaState.AWAITING_ANSWER:
        # The AI asked a question — the whole message IS the answer, unless the user backs out.
        if low in _ABORT_PHRASES or low in _CANCEL_WORDS:
            return Intent("abort")
        return Intent(
            "answer",
            {
                "answer": raw,
                "mission_key": session.context.get("mission_key"),
                "blocker_id": session.context.get("blocker_id"),
            },
        )

    if session.state == WaState.DRAFTING_MISSION:
        # We asked "what should I build?"; the whole message is the brief (help/abort still escape).
        if low in _HELP_WORDS:
            return Intent("help")
        if low in _ABORT_PHRASES or low in _CANCEL_WORDS:
            return Intent("abort")
        return Intent("start", {"brief": raw})

    # --- default (IDLE) grammar ---------------------------------------------------
    parts = low.split()
    first = parts[0] if parts else ""

    # Back-out phrases before 'cancel' so "cancel that" aborts rather than cancelling a mission.
    if low in _ABORT_PHRASES:
        return Intent("abort")
    if first == "confirm":
        return Intent("confirm")
    if first in _HELP_WORDS or low in _HELP_WORDS:
        return Intent("help")

    if first in _START_WORDS:
        brief = raw.split(None, 1)[1].strip() if len(parts) > 1 else ""
        return Intent("start", {"brief": brief})
    if first in _CANCEL_WORDS:
        return Intent("cancel", {"mission_key": extract_key(raw)})
    if first == "retry":
        return Intent("retry", {"mission_key": extract_key(raw)})
    if first in _APPROVE_WORDS:
        return Intent("approve", {"mission_key": extract_key(raw)})
    if first in _REJECT_WORDS:
        return Intent("reject", {"mission_key": extract_key(raw)})
    # Read-only org views (Tier 1): teams / agents / skills / models, and "<thing> <name>" details.
    rest = raw.split(None, 1)[1].strip() if len(parts) > 1 else ""
    if first == "list" and len(parts) > 1 and parts[1] in _LIST_TARGETS:
        return Intent(_LIST_TARGETS[parts[1]])
    if first == "teams":
        return Intent("teams")
    if first == "agents":
        return Intent("agents")
    if first == "skills":
        return Intent("skills")
    if first in {"models", "model"}:
        return Intent("models")
    if first == "team":
        return Intent("team", {"name": rest}) if rest else Intent("teams")
    if first == "agent":
        return Intent("agent", {"name": rest}) if rest else Intent("agents")
    if first == "status" or "status" in parts:
        return Intent("status")
    if first in {"missions", "list"}:
        return Intent("missions")
    if first == "mission":
        return Intent("mission", {"mission_key": extract_key(raw)})

    # A message that is essentially just a key ("M-152") → look that mission up.
    key = extract_key(raw)
    if key is not None:
        return Intent("mission", {"mission_key": key})

    return Intent("unknown")


def advance(session: WaSessionState, intent: Intent) -> tuple[str, dict | None]:
    """Apply an intent to the session (MUTATING it in place) and return ``(reply_text, action)``.

    ``action`` is ``None`` for pure conversational turns (help, prompts, ambiguity). Otherwise it is a
    small dict describing what :func:`app.wa_control.execute` should do, e.g.
    ``{"do": "start", "brief": ...}`` or ``{"do": "cancel", "mission_key": ...}``. When an action is
    returned, the reply is a transient acknowledgement — the caller should surface the executor's
    result as the real reply."""
    kind = intent.kind
    args = intent.args

    if kind == "help":
        return _HELP_TEXT, None

    if kind == "start":
        brief = (args.get("brief") or "").strip()
        if not brief:
            # No brief yet — remember we're drafting and ask for it (uses DRAFTING_MISSION).
            session.state = WaState.DRAFTING_MISSION
            session.context = {}
            return "What should I build? Send me the brief, or 'abort' to stop.", None
        # Stage the destructive action; only a follow-up 'confirm' actually starts it.
        session.state = WaState.AWAITING_CONFIRM
        session.context = {"pending": "start", "draft_brief": brief}
        return (
            f"Reply 'confirm' to start a mission for: \"{brief}\".\n(Or 'abort' to cancel.)",
            None,
        )

    if kind == "cancel":
        key = args.get("mission_key")
        session.state = WaState.AWAITING_CONFIRM
        session.context = {"pending": "cancel", "mission_key": key}
        target = key or "the current mission"
        return f"Reply 'confirm' to stop {target}.\n(Or 'abort' to keep it running.)", None

    if kind == "confirm":
        pending = session.context.get("pending")
        if session.state != WaState.AWAITING_CONFIRM or not pending:
            return "Nothing to confirm right now. Send 'help' for what I can do.", None
        if pending == "start":
            brief = session.context.get("draft_brief", "")
            _reset(session)
            return (
                "Starting… I'll message you when I need approvals or have questions.",
                {"do": "start", "brief": brief},
            )
        if pending == "cancel":
            key = session.context.get("mission_key")
            _reset(session)
            return "Stopping…", {"do": "cancel", "mission_key": key}
        _reset(session)
        return "Okay, done.", None

    if kind == "abort":
        _reset(session)
        return "Okay, cancelled that.", None

    if kind == "answer":
        _reset(session)  # one answer per prompt; return to idle
        return (
            "Got it — passing your answer along.",
            {
                "do": "answer",
                "mission_key": args.get("mission_key"),
                "blocker_id": args.get("blocker_id"),
                "answer": args.get("answer", ""),
            },
        )

    if kind == "approve":
        # Approve/reject are non-destructive gate decisions — safe to execute immediately.
        return "Approving…", {"do": "approve", "mission_key": args.get("mission_key")}

    if kind == "reject":
        return "Rejecting…", {"do": "reject", "mission_key": args.get("mission_key")}

    if kind == "retry":
        return "Retrying…", {"do": "retry", "mission_key": args.get("mission_key")}

    if kind == "status":
        return "Checking…", {"do": "status"}

    if kind == "missions":
        return "Checking…", {"do": "missions"}

    if kind == "mission":
        return "Checking…", {"do": "mission", "mission_key": args.get("mission_key")}

    # Read-only org views (Tier 1) — the executor produces the real reply.
    if kind == "teams":
        return "Checking…", {"do": "teams"}
    if kind == "agents":
        return "Checking…", {"do": "agents"}
    if kind == "skills":
        return "Checking…", {"do": "skills"}
    if kind == "models":
        return "Checking…", {"do": "models"}
    if kind == "team":
        return "Checking…", {"do": "team", "name": args.get("name", "")}
    if kind == "agent":
        return "Checking…", {"do": "agent", "name": args.get("name", "")}

    # Unknown → nudge with usage rather than guessing at an action.
    return "Sorry, I didn't catch that.\n\n" + _HELP_TEXT, None


def _reset(session: WaSessionState) -> None:
    """Return a session to a clean IDLE state (used after an action fires or is aborted)."""
    session.state = WaState.IDLE
    session.context = {}
