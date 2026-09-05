"""AI eval gates run in CI (plan 12 §5.7): each suite's mean must clear its manifest gate.

Runs offline against the deterministic provider, so the gate is reproducible in CI. A prompt,
model-binding, tool, or agent-loop change that regresses quality flips these red.
"""

from __future__ import annotations

import pytest
from evals.harness.runner import run_suite

SUITES = ["backend-diff-passes-stories", "pm-spec-quality"]


@pytest.mark.parametrize("suite", SUITES)
async def test_eval_gate_passes(suite: str, tmp_path) -> None:
    run = await run_suite(suite, samples=1, mode="gate", sandbox_root=str(tmp_path))
    assert run.cases, f"{suite} has no cases"
    assert run.passed, f"{suite} gate failed: mean {run.mean_score:.3f} < {run.gate_min}"
    for case in run.cases:
        assert case.budget_ok, f"{suite}/{case.case_id} exceeded its cost/latency budget"
