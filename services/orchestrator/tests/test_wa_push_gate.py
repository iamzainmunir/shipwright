"""WhatsApp handling of the push-rejected merge gate (P4 follow-up).

When a merge push is rejected, the engine re-gates asking the user to authorize a force-push OR push a
different branch. This exercises: the session, once armed for that gate, understands 'force push' and
'new branch <name>' as decisions (not a new mission), and the executor resolves the blocker with the
right branch/force — while an IDLE 'create …' still starts a mission (no regression)."""
from __future__ import annotations

from app import wa_control
from app.wa_session import Intent, WaSessionState, WaState, advance, parse_intent


def _armed(**ctx) -> WaSessionState:
    return WaSessionState(sender="user:1", state=WaState.AWAITING_PUSH_DECISION,
                          context={"mission_key": "M-1", "blocker_id": "B1", **ctx})


# --- parsing (pure logic) --------------------------------------------------------

def test_force_push_is_a_push_decision():
    i = parse_intent("force push", _armed())
    assert i.kind == "push_decision" and i.args["mode"] == "force"
    assert i.args["mission_key"] == "M-1" and i.args["blocker_id"] == "B1"


def test_create_new_branch_is_a_branch_decision_not_a_new_mission():
    i = parse_intent("create new branch", _armed())
    assert i.kind == "push_decision" and i.args["mode"] == "branch" and i.args.get("branch", "") == ""


def test_new_branch_with_explicit_name():
    i = parse_intent("new branch feature-x", _armed())
    assert i.kind == "push_decision" and i.args["mode"] == "branch" and i.args["branch"] == "feature-x"


def test_armed_abort_resets_to_idle():
    s = _armed()
    reply, action = advance(s, parse_intent("abort", s))
    assert action is None and s.state == WaState.IDLE


def test_advance_force_returns_action_and_resets():
    s = _armed()
    reply, action = advance(s, Intent("push_decision", {"mode": "force", "mission_key": "M-1", "blocker_id": "B1"}))
    assert action == {"do": "push_decision", "mode": "force", "branch": "",
                      "mission_key": "M-1", "blocker_id": "B1"}
    assert s.state == WaState.IDLE


def test_idle_create_still_starts_a_mission():
    # Regression: outside the push gate, 'create …' must still mean start a mission.
    i = parse_intent("create a todo app", WaSessionState(sender="user:1"))
    assert i.kind == "start"


# --- executor --------------------------------------------------------------------

class _FakeEngine:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def resolve_blocker(self, blocker_id, decision, *, actor, note,
                              repo=None, branch=None, force=False):
        self.calls.append({"blocker_id": blocker_id,
                           "decision": getattr(decision, "value", decision),
                           "branch": branch, "force": force})


async def test_execute_branch_resolves_with_branch_no_force():
    eng = _FakeEngine()
    await wa_control.execute(
        {"do": "push_decision", "mode": "branch", "branch": "feature-x",
         "mission_key": "M-1", "blocker_id": "B1"},
        store=None, engine=eng, actor="whatsapp:1", workspace_id="ws")
    assert eng.calls and eng.calls[0]["branch"] == "feature-x"
    assert eng.calls[0]["force"] is False and eng.calls[0]["decision"] == "approve"


async def test_execute_force_resolves_with_force():
    eng = _FakeEngine()
    await wa_control.execute(
        {"do": "push_decision", "mode": "force", "branch": "",
         "mission_key": "M-1", "blocker_id": "B1"},
        store=None, engine=eng, actor="whatsapp:1", workspace_id="ws")
    assert eng.calls and eng.calls[0]["force"] is True


async def test_execute_branch_defaults_a_name_when_omitted():
    eng = _FakeEngine()
    await wa_control.execute(
        {"do": "push_decision", "mode": "branch", "branch": "",
         "mission_key": "M-7", "blocker_id": "B1"},
        store=None, engine=eng, actor="whatsapp:1", workspace_id="ws")
    assert eng.calls and eng.calls[0]["branch"]  # a non-empty branch name was generated
    assert eng.calls[0]["force"] is False
