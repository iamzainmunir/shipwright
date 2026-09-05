"""Typed value objects for the eval harness (plan 12 §5.2/§5.3).

Small, immutable dataclasses so the loader, scorers, runner, and report each depend on a
shared vocabulary rather than on dicts. Scores are normalized to 0.0–1.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Budget:
    """Per-case cost/latency ceilings from the suite manifest (plan 12 §5.6)."""

    max_cost_cents_per_case: int = 50
    max_latency_ms_p50: int = 60_000


@dataclass(frozen=True, slots=True)
class Gate:
    """Release-gate thresholds (plan 12 §5.7)."""

    metric: str = "overall_score"
    min: float = 0.8
    samples: int = 1


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One labeled input + its success oracle."""

    id: str
    input: dict
    expected: dict
    labels: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvalSuite:
    """A dataset: manifest metadata + the cases (plan 12 §5.2, Canon §13.12)."""

    id: str
    version: int
    target: dict  # {role, phase}
    budget: Budget
    gate: Gate
    cases: list[EvalCase]


@dataclass(frozen=True, slots=True)
class Score:
    """A scorer's verdict for one agent output."""

    value: float  # 0.0–1.0
    passed: bool
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SampleResult:
    """One sample of one case (agents run at production temperature, so we take N)."""

    case_id: str
    sample: int
    score: float
    passed: bool
    cost_cents: int
    latency_ms: int
    budget_ok: bool
    detail: dict = field(default_factory=dict)


@dataclass(slots=True)
class CaseAggregate:
    """A case's results aggregated over its samples."""

    case_id: str
    mean_score: float
    pass_rate: float
    samples: int
    cost_cents_p50: int
    budget_ok: bool


@dataclass(slots=True)
class SuiteResult:
    """The outcome of running a suite, with the gate decision already applied."""

    suite_id: str
    version: int
    samples: int
    mode: str
    gate_min: float
    mean_score: float
    passed: bool
    cases: list[CaseAggregate]
