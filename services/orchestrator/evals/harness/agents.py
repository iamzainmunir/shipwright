"""Invoke the agent under test for a case, returning a uniform :class:`AgentOutput`.

Dispatches by the suite's ``target.role``. Tools run against a throwaway sandbox (backend)
or a single provider call (pm); with the deterministic offline provider this is fully
reproducible and free of network/side effects (plan 12 §5.3).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app import devloop
from app.providers import LLMProvider
from app.seed import DEMO_ORG, DEMO_WS
from foundry_core.enums import AutonomyLevel, MissionSource, Priority
from foundry_core.ids import new_ulid
from foundry_core.models import Mission

from .models import EvalCase, EvalSuite


@dataclass(slots=True)
class AgentOutput:
    """Everything a scorer might need, regardless of which agent produced it."""

    text: str = ""
    diff: str = ""
    files: list[str] = field(default_factory=list)
    tests_passed: bool = False
    cost_cents: int = 0
    latency_ms: int = 0


def _mission_from_case(case: EvalCase) -> Mission:
    ticket = case.input.get("ticket", {})
    return Mission(
        id=new_ulid(), key=case.id, org_id=DEMO_ORG, workspace_id=DEMO_WS,
        title=ticket.get("title", case.id), summary=ticket.get("body"),
        source=MissionSource.JIRA, priority=Priority.P2, autonomy=AutonomyLevel.SUPERVISED,
    )


async def invoke_agent(
    suite: EvalSuite, case: EvalCase, provider: LLMProvider, *, sandbox_root: str | None = None
) -> AgentOutput:
    role = suite.target.get("role")
    started = time.monotonic()
    if role == "backend":
        return await _invoke_backend(case, provider, sandbox_root, started)
    if role == "pm":
        return await _invoke_pm(case, provider, started)
    raise ValueError(f"no eval agent wired for target role {role!r}")


async def _invoke_backend(
    case: EvalCase, provider: LLMProvider, sandbox_root: str | None, started: float
) -> AgentOutput:
    mission = _mission_from_case(case)
    result, sandbox = await devloop.real_build(mission, provider, sandbox_root=sandbox_root)
    try:
        return AgentOutput(
            text=result.summary, diff=result.diff, files=result.files,
            tests_passed=result.tests_passed, cost_cents=result.cost_cents,
            latency_ms=_elapsed_ms(started),
        )
    finally:
        await sandbox.destroy()


async def _invoke_pm(case: EvalCase, provider: LLMProvider, started: float) -> AgentOutput:
    ticket = case.input.get("ticket", {})
    prompt = (
        f"{ticket.get('title', case.id)}\n\n"
        f"Summary: {ticket.get('body', '(none)')}\n"
        "Phase: spec. Produce the spec output for this ticket."
    )
    result = await provider.complete(
        system="You are a product manager. Be concise and concrete.",
        prompt=prompt, purpose="spec",
    )
    return AgentOutput(text=result.text, cost_cents=result.cost_cents, latency_ms=_elapsed_ms(started))


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
