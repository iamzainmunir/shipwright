"""Run a suite: for each case, sample the agent N times, score, aggregate, gate (plan 12 §5.3)."""

from __future__ import annotations

from pathlib import Path
from statistics import mean

from app.providers import LLMProvider

from .agents import invoke_agent
from .loader import load_suite
from .models import CaseAggregate, EvalCase, EvalSuite, SampleResult, SuiteResult
from .scorers import score_case
from .scripted_provider import ScriptedProvider


async def run_suite(
    suite: str | Path, *, samples: int | None = None, mode: str = "gate",
    provider: LLMProvider | None = None, sandbox_root: str | None = None,
) -> SuiteResult:
    """Execute every case ``samples`` times and apply the manifest gate.

    Offline and deterministic by default (the harness ``ScriptedProvider`` — testing infra, not a
    product provider). ``samples`` overrides the manifest's ``gate.samples`` when given.
    """
    loaded = load_suite(suite)
    provider = provider or ScriptedProvider()
    n = samples or loaded.gate.samples

    aggregates = [
        await _run_case(loaded, case, provider, n, sandbox_root)
        for case in loaded.cases
    ]
    suite_mean = mean(a.mean_score for a in aggregates) if aggregates else 0.0
    within_budget = all(a.budget_ok for a in aggregates)
    passed = bool(aggregates) and suite_mean >= loaded.gate.min and within_budget
    return SuiteResult(
        suite_id=loaded.id, version=loaded.version, samples=n, mode=mode,
        gate_min=loaded.gate.min, mean_score=suite_mean, passed=passed, cases=aggregates,
    )


async def _run_case(
    suite: EvalSuite, case: EvalCase, provider: LLMProvider, samples: int, sandbox_root: str | None
) -> CaseAggregate:
    results: list[SampleResult] = []
    for s in range(samples):
        out = await invoke_agent(suite, case, provider, sandbox_root=sandbox_root)
        score = score_case(suite, case, out)
        budget_ok = (
            out.cost_cents <= suite.budget.max_cost_cents_per_case
            and out.latency_ms <= suite.budget.max_latency_ms_p50
        )
        results.append(
            SampleResult(case.id, s, score.value, score.passed, out.cost_cents, out.latency_ms,
                         budget_ok, score.detail)
        )
    return CaseAggregate(
        case_id=case.id,
        mean_score=mean(r.score for r in results),
        pass_rate=mean(1.0 if r.passed else 0.0 for r in results),
        samples=samples,
        cost_cents_p50=_p50([r.cost_cents for r in results]),
        budget_ok=all(r.budget_ok for r in results),
    )


def _p50(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]
