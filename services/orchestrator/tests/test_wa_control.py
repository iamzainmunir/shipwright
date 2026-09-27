"""Tests for the WhatsApp control executor: it drives injected engine/store fakes and NEVER raises.

pytest runs with ``asyncio_mode="auto"`` — ``async def test_...`` needs no marker."""
from __future__ import annotations

from typing import Any

from app.wa_control import execute
from foundry_core.enums import ApprovalDecision, BlockerKind, MissionStage

_WS = "ws-1"
_ACTOR = "whatsapp:+15550001"


# --- fakes ----------------------------------------------------------------------


class FakeMission:
    def __init__(
        self,
        id: str,
        key: str,
        *,
        workspace_id: str = _WS,
        stage: MissionStage = MissionStage.BUILDING,
        title: str = "Todo app",
        progress: int = 42,
    ) -> None:
        self.id = id
        self.key = key
        self.workspace_id = workspace_id
        self.stage = stage
        self.title = title
        self.progress = progress


class FakeBlocker:
    def __init__(
        self,
        id: str,
        mission_id: str,
        *,
        workspace_id: str = _WS,
        kind: BlockerKind = BlockerKind.APPROVAL,
        resolved_at: Any = None,
    ) -> None:
        self.id = id
        self.mission_id = mission_id
        self.workspace_id = workspace_id
        self.kind = kind
        self.resolved_at = resolved_at


class FakeStore:
    def __init__(
        self, missions: list[FakeMission] | None = None, blockers: list[FakeBlocker] | None = None
    ) -> None:
        self._by_key = {m.key: m for m in (missions or [])}
        self._by_id = {m.id: m for m in (missions or [])}
        self._blockers = list(blockers or [])

    async def get_mission(self, key_or_id: str) -> FakeMission | None:
        return self._by_key.get(key_or_id) or self._by_id.get(key_or_id)

    async def list_missions(self, workspace_id: str = _WS) -> list[FakeMission]:
        return [m for m in self._by_key.values() if m.workspace_id == workspace_id]

    async def list_blockers(
        self, workspace_id: str = _WS, *, unresolved_only: bool = True
    ) -> list[FakeBlocker]:
        out = [b for b in self._blockers if (b.workspace_id or _WS) == workspace_id]
        if unresolved_only:
            out = [b for b in out if b.resolved_at is None]
        return out


class FakeEngine:
    """Records every call so tests can assert the executor wired the right method + args."""

    def __init__(self, *, mission_key: str = "M-900") -> None:
        self.calls: list[tuple] = []
        self._mission_key = mission_key

    async def start_mission_from_text(
        self, brief: str, *, actor: str, workspace_id: str
    ) -> FakeMission:
        self.calls.append(("start_mission_from_text", brief, actor, workspace_id))
        return FakeMission("m-900", self._mission_key, workspace_id=workspace_id)

    async def cancel_run(self, mission: FakeMission, *, actor: str | None = None) -> int:
        self.calls.append(("cancel_run", mission.key, actor))
        return 1

    async def retry_run(self, mission: FakeMission, *, from_start: bool = False) -> object:
        self.calls.append(("retry_run", mission.key, from_start))
        return object()

    async def submit_clarification(
        self, blocker_id: str, answers: list[dict], *, actor: str | None = None
    ) -> object:
        self.calls.append(("submit_clarification", blocker_id, answers, actor))
        return object()

    async def resolve_blocker(
        self, blocker_id: str, decision: ApprovalDecision, *, actor: str, note: str, **kw: object
    ) -> object:
        self.calls.append(("resolve_blocker", blocker_id, decision, actor, note))
        return object()


# --- start ----------------------------------------------------------------------


async def test_start_calls_engine_and_returns_started_reply() -> None:
    store, engine = FakeStore(), FakeEngine(mission_key="M-901")
    reply = await execute(
        {"do": "start", "brief": "build a todo app"},
        store=store, engine=engine, actor=_ACTOR, workspace_id=_WS,
    )
    assert ("start_mission_from_text", "build a todo app", _ACTOR, _WS) in engine.calls
    assert "Started" in reply and "M-901" in reply


async def test_start_without_start_method_is_not_wired() -> None:
    class NoStartEngine:  # only has cancel — no way to start
        async def cancel_run(self, mission: Any, *, actor: str | None = None) -> int:
            return 0

    reply = await execute(
        {"do": "start", "brief": "x"},
        store=FakeStore(), engine=NoStartEngine(), actor=_ACTOR, workspace_id=_WS,
    )
    assert "isn't wired" in reply


# --- cancel ---------------------------------------------------------------------


async def test_cancel_calls_cancel_run() -> None:
    store = FakeStore(missions=[FakeMission("m1", "M-152")])
    engine = FakeEngine()
    reply = await execute(
        {"do": "cancel", "mission_key": "M-152"},
        store=store, engine=engine, actor=_ACTOR, workspace_id=_WS,
    )
    assert ("cancel_run", "M-152", _ACTOR) in engine.calls
    assert "Stopped M-152" in reply


async def test_cancel_unknown_mission() -> None:
    reply = await execute(
        {"do": "cancel", "mission_key": "M-999"},
        store=FakeStore(), engine=FakeEngine(), actor=_ACTOR, workspace_id=_WS,
    )
    assert "Couldn't find M-999" in reply


# --- answer ---------------------------------------------------------------------


async def test_answer_calls_submit_clarification() -> None:
    store = FakeStore(missions=[FakeMission("m1", "M-152")])
    engine = FakeEngine()
    reply = await execute(
        {"do": "answer", "mission_key": "M-152", "blocker_id": "B1", "answer": "use postgres"},
        store=store, engine=engine, actor=_ACTOR, workspace_id=_WS,
    )
    submit = [c for c in engine.calls if c[0] == "submit_clarification"]
    assert submit and submit[0][1] == "B1"
    assert submit[0][2] == [{"answer": "use postgres"}]
    assert "M-152" in reply


# --- approve / reject -----------------------------------------------------------


async def test_approve_resolves_open_approval_blocker() -> None:
    store = FakeStore(
        missions=[FakeMission("m1", "M-152")],
        blockers=[FakeBlocker("B1", "m1", kind=BlockerKind.APPROVAL)],
    )
    engine = FakeEngine()
    reply = await execute(
        {"do": "approve", "mission_key": "M-152"},
        store=store, engine=engine, actor=_ACTOR, workspace_id=_WS,
    )
    resolved = [c for c in engine.calls if c[0] == "resolve_blocker"]
    assert resolved and resolved[0][1] == "B1"
    assert resolved[0][2] == ApprovalDecision.APPROVE
    assert "Approved M-152" in reply


async def test_reject_resolves_with_reject_decision() -> None:
    store = FakeStore(
        missions=[FakeMission("m1", "M-152")],
        blockers=[FakeBlocker("B1", "m1")],
    )
    engine = FakeEngine()
    reply = await execute(
        {"do": "reject", "mission_key": "M-152"},
        store=store, engine=engine, actor=_ACTOR, workspace_id=_WS,
    )
    resolved = [c for c in engine.calls if c[0] == "resolve_blocker"]
    assert resolved and resolved[0][2] == ApprovalDecision.REJECT
    assert "Rejected M-152" in reply


async def test_approve_with_no_pending_blocker() -> None:
    store = FakeStore(missions=[FakeMission("m1", "M-152")])  # no blockers
    reply = await execute(
        {"do": "approve", "mission_key": "M-152"},
        store=store, engine=FakeEngine(), actor=_ACTOR, workspace_id=_WS,
    )
    assert "No pending approval on M-152" in reply


async def test_approve_ignores_non_approval_and_resolved_blockers() -> None:
    store = FakeStore(
        missions=[FakeMission("m1", "M-152")],
        blockers=[
            FakeBlocker("B1", "m1", kind=BlockerKind.QUESTION),  # wrong kind
            FakeBlocker("B2", "m1", resolved_at="2026-01-01"),   # already resolved (filtered out)
        ],
    )
    reply = await execute(
        {"do": "approve", "mission_key": "M-152"},
        store=store, engine=FakeEngine(), actor=_ACTOR, workspace_id=_WS,
    )
    assert "No pending approval on M-152" in reply


# --- status ---------------------------------------------------------------------


async def test_status_summarizes_by_stage() -> None:
    store = FakeStore(
        missions=[
            FakeMission("m1", "M-1", stage=MissionStage.BUILDING),
            FakeMission("m2", "M-2", stage=MissionStage.SHIPPED),
        ]
    )
    reply = await execute(
        {"do": "status"}, store=store, engine=FakeEngine(), actor=_ACTOR, workspace_id=_WS
    )
    assert "2 mission(s)" in reply
    assert "building" in reply and "shipped" in reply


# --- failure isolation: execute NEVER raises ------------------------------------


async def test_execute_returns_safe_string_when_engine_method_missing() -> None:
    class BareEngine:  # missing cancel_run entirely
        pass

    reply = await execute(
        {"do": "cancel", "mission_key": "M-152"},
        store=FakeStore(missions=[FakeMission("m1", "M-152")]),
        engine=BareEngine(), actor=_ACTOR, workspace_id=_WS,
    )
    assert isinstance(reply, str) and "Can't stop" in reply


async def test_execute_never_raises_on_store_error() -> None:
    class ExplodingStore:
        async def get_mission(self, key: str) -> Any:
            raise RuntimeError("boom")

    reply = await execute(
        {"do": "cancel", "mission_key": "M-1"},
        store=ExplodingStore(), engine=FakeEngine(), actor=_ACTOR, workspace_id=_WS,
    )
    assert reply == "Sorry, something went wrong handling that."


async def test_unknown_action_returns_hint() -> None:
    reply = await execute(
        {"do": "frobnicate"}, store=FakeStore(), engine=FakeEngine(), actor=_ACTOR, workspace_id=_WS
    )
    assert isinstance(reply, str) and "help" in reply.lower()


async def test_empty_action_is_safe() -> None:
    reply = await execute({}, store=FakeStore(), engine=FakeEngine(), actor=_ACTOR, workspace_id=_WS)
    assert isinstance(reply, str) and reply
