"""Tests for the WhatsApp session brain: intent parsing (state-aware) + the state machine.

pytest runs with ``asyncio_mode="auto"``; these are plain sync tests (pure logic, no I/O)."""
from __future__ import annotations

import pytest
from app.wa_session import (
    Intent,
    WaSessionState,
    WaState,
    advance,
    extract_key,
    parse_intent,
)


def _session(state: WaState = WaState.IDLE, **context: object) -> WaSessionState:
    return WaSessionState(sender="whatsapp:+15550001", state=state, context=dict(context))


# --- parse_intent: representative messages across states ------------------------

@pytest.mark.parametrize(
    ("text", "state", "kind"),
    [
        # IDLE grammar
        ("start build a todo app", WaState.IDLE, "start"),
        ("build a notes app", WaState.IDLE, "start"),
        ("create an invoicing tool", WaState.IDLE, "start"),
        ("cancel M-152", WaState.IDLE, "cancel"),
        ("stop", WaState.IDLE, "cancel"),
        ("retry FND-142", WaState.IDLE, "retry"),
        ("approve M-152", WaState.IDLE, "approve"),
        ("yes", WaState.IDLE, "approve"),
        ("reject M-152", WaState.IDLE, "reject"),
        ("no", WaState.IDLE, "reject"),
        ("status", WaState.IDLE, "status"),
        ("missions", WaState.IDLE, "missions"),
        ("list", WaState.IDLE, "missions"),
        ("mission M-152", WaState.IDLE, "mission"),
        ("M-152", WaState.IDLE, "mission"),
        ("help", WaState.IDLE, "help"),
        ("?", WaState.IDLE, "help"),
        ("cancel that", WaState.IDLE, "abort"),
        ("totally unrelated gibberish", WaState.IDLE, "unknown"),
        ("", WaState.IDLE, "help"),
        # AWAITING_CONFIRM: only yes/confirm proceeds; everything else aborts.
        ("confirm", WaState.AWAITING_CONFIRM, "confirm"),
        ("yes", WaState.AWAITING_CONFIRM, "confirm"),
        ("no", WaState.AWAITING_CONFIRM, "abort"),
        ("start something else", WaState.AWAITING_CONFIRM, "abort"),
        # AWAITING_ANSWER: free text is the answer; explicit cancel/abort escapes.
        ("use postgres and redis", WaState.AWAITING_ANSWER, "answer"),
        ("cancel", WaState.AWAITING_ANSWER, "abort"),
        # DRAFTING_MISSION: the whole message becomes the brief.
        ("a kanban board", WaState.DRAFTING_MISSION, "start"),
    ],
)
def test_parse_intent_kind(text: str, state: WaState, kind: str) -> None:
    assert parse_intent(text, _session(state)).kind == kind


def test_start_captures_brief() -> None:
    intent = parse_intent("start build a todo app", _session())
    assert intent.kind == "start"
    assert intent.args["brief"] == "build a todo app"


def test_slash_commands_are_accepted() -> None:
    # WhatsApp agents commonly use slash-commands — a leading "/" must not break classification.
    assert parse_intent("/help", _session()).kind == "help"
    assert parse_intent("/status", _session()).kind == "status"
    si = parse_intent("/start build a todo app", _session())
    assert si.kind == "start" and si.args["brief"] == "build a todo app"


def test_cancel_and_mission_capture_key() -> None:
    assert parse_intent("cancel M-152", _session()).args["mission_key"] == "M-152"
    assert parse_intent("mission FND-142", _session()).args["mission_key"] == "FND-142"
    assert parse_intent("stop", _session()).args["mission_key"] is None


def test_awaiting_answer_carries_context() -> None:
    session = _session(WaState.AWAITING_ANSWER, mission_key="M-152", blocker_id="B1")
    intent = parse_intent("use postgres", session)
    assert intent.kind == "answer"
    assert intent.args == {"answer": "use postgres", "mission_key": "M-152", "blocker_id": "B1"}


def test_extract_key() -> None:
    assert extract_key("cancel M-152") == "M-152"
    assert extract_key("mission FND-142 please") == "FND-142"
    assert extract_key("lowercase m-152 works too") == "M-152"
    assert extract_key("no key here") is None
    assert extract_key("") is None


# --- advance: the destructive-confirm flow --------------------------------------

def test_start_requires_confirm_then_emits_action() -> None:
    session = _session()
    reply, action = advance(session, parse_intent("start build a todo app", session))
    # First message NEVER starts — it stages a confirm.
    assert action is None
    assert session.state == WaState.AWAITING_CONFIRM
    assert "confirm" in reply.lower()

    # A follow-up "confirm" is what actually fires the start action.
    reply2, action2 = advance(session, parse_intent("confirm", session))
    assert action2 == {"do": "start", "brief": "build a todo app"}
    assert session.state == WaState.IDLE


def test_cancel_requires_confirm_then_emits_action() -> None:
    session = _session()
    _, action = advance(session, parse_intent("cancel M-152", session))
    assert action is None
    assert session.state == WaState.AWAITING_CONFIRM

    _, action2 = advance(session, parse_intent("confirm", session))
    assert action2 == {"do": "cancel", "mission_key": "M-152"}
    assert session.state == WaState.IDLE


def test_abort_resets_pending_action() -> None:
    session = _session()
    advance(session, parse_intent("start build a todo app", session))
    reply, action = advance(session, parse_intent("nope, forget it", session))
    assert action is None
    assert session.state == WaState.IDLE
    assert reply == "Okay, cancelled that."


def test_start_without_brief_asks_for_it() -> None:
    session = _session()
    reply, action = advance(session, parse_intent("start", session))
    assert action is None
    assert session.state == WaState.DRAFTING_MISSION
    # The next message is captured as the brief and staged for confirm.
    reply2, action2 = advance(session, parse_intent("a kanban board", session))
    assert action2 is None
    assert session.state == WaState.AWAITING_CONFIRM
    assert session.context["draft_brief"] == "a kanban board"


def test_answer_flow_emits_answer_action_and_resets() -> None:
    session = _session(WaState.AWAITING_ANSWER, mission_key="M-152", blocker_id="B1")
    reply, action = advance(session, parse_intent("use postgres and redis", session))
    assert action == {
        "do": "answer",
        "mission_key": "M-152",
        "blocker_id": "B1",
        "answer": "use postgres and redis",
    }
    assert session.state == WaState.IDLE


def test_approve_reject_status_emit_actions_immediately() -> None:
    session = _session()
    assert advance(session, parse_intent("approve M-152", session))[1] == {
        "do": "approve", "mission_key": "M-152",
    }
    assert advance(session, parse_intent("reject M-152", session))[1] == {
        "do": "reject", "mission_key": "M-152",
    }
    assert advance(session, parse_intent("status", session))[1] == {"do": "status"}


def test_help_and_unknown_are_conversational() -> None:
    session = _session()
    reply, action = advance(session, Intent("help"))
    assert action is None and "start" in reply.lower()

    reply2, action2 = advance(session, Intent("unknown"))
    # Unknown falls back to the usage hint rather than guessing at an action.
    assert action2 is None
    assert "didn't catch" in reply2.lower() and "start <brief>" in reply2


def test_confirm_with_nothing_pending_is_safe() -> None:
    session = _session()  # IDLE, no pending
    reply, action = advance(session, Intent("confirm"))
    assert action is None
    assert "nothing to confirm" in reply.lower()
