"""P0 trust & guardrails — the runaway-cost ceiling and the cap-derived pipeline safety stop.

These are the additive guardrails from the 2026-09-26 audit (A3 cost budget never enforced; A4 magic
phase-count backstop). The verification-gate hardening (A2 fail-closed, A15 red-tests-block) is a
separate PR because it changes routing semantics.
"""
from __future__ import annotations

from app import engine as eng
from app.engine import (
    _PIPELINE_SAFETY_STOP,
    MAX_CTO_DECISIONS,
    MAX_ESCALATIONS,
    MAX_REWORK_CYCLES,
    RunEngine,
)
from app.events import EventBus
from app.store import InMemoryStore

from tests.support.scripted_provider import ScriptedProvider


def test_safety_stop_is_derived_from_caps_not_magic():
    # A4: the backstop must be a function of the real loop caps (an OUTER net), not a hand-picked 40.
    expected = (
        (MAX_CTO_DECISIONS + 1) * (eng._CANON_PHASE_COUNT + 2 * MAX_REWORK_CYCLES + MAX_ESCALATIONS)
        + eng.MAX_PUSH_REGATES + eng.MAX_ERROR_ESCALATIONS
    )
    assert _PIPELINE_SAFETY_STOP == expected
    # It must comfortably exceed the worst-case real convergence bound so it can only ever be a net.
    assert _PIPELINE_SAFETY_STOP > (MAX_CTO_DECISIONS + 1) * (2 * MAX_REWORK_CYCLES + MAX_ESCALATIONS)


def _engine() -> RunEngine:
    return RunEngine(InMemoryStore(), EventBus(), ScriptedProvider())


def test_over_budget_off_by_default():
    # A3: 0 = uncapped (Rule 0). No ceiling ⇒ never over budget, whatever the spend.
    engine = _engine()
    engine._run_cost_budget = 0
    assert engine._over_budget(0) is False
    assert engine._over_budget(10_000_000) is False


def test_over_budget_trips_at_ceiling():
    engine = _engine()
    engine._run_cost_budget = 500  # $5.00
    assert engine._over_budget(0) is False
    assert engine._over_budget(499) is False
    assert engine._over_budget(500) is True   # at the ceiling ⇒ stop before more spend
    assert engine._over_budget(750) is True


def test_over_budget_tolerates_missing_cost():
    engine = _engine()
    engine._run_cost_budget = 500
    assert engine._over_budget(None) is False  # a run with no cost recorded yet is not over budget
