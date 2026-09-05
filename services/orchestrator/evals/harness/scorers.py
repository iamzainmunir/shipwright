"""Programmatic scorers (plan 12 §5.4). Objective oracles, preferred over judges.

Each returns a :class:`Score` in 0.0–1.0 with a ``detail`` dict for the report. We keep to
objective checks here because the offline harness has no judge model; a real deployment adds
LLM-as-judge rubrics alongside these programmatic floors.
"""

from __future__ import annotations

import re

from .agents import AgentOutput
from .models import EvalCase, EvalSuite, Score

_STORY_LINE = re.compile(r"(?m)^\s*\d+\.")  # a numbered acceptance story
_SPEC_HEADER = re.compile(r"#\s*spec", re.IGNORECASE)


def score_case(suite: EvalSuite, case: EvalCase, out: AgentOutput) -> Score:
    role = suite.target.get("role")
    if role == "backend":
        return _score_backend_diff(case, out)
    if role == "pm":
        return _score_spec_quality(case, out)
    raise ValueError(f"no scorer for target role {role!r}")


def _score_backend_diff(case: EvalCase, out: AgentOutput) -> Score:
    """The strongest eval: the diff must apply and the acceptance tests must pass."""
    if not out.files:
        return Score(0.0, False, {"reason": "no files changed"})
    if not out.tests_passed:
        return Score(0.0, False, {"reason": "acceptance tests failed", "files": out.files})
    programmatic = case.expected.get("programmatic", {})
    must_change = programmatic.get("mustChangeFile")
    if must_change and must_change not in out.files:
        return Score(0.5, False, {"reason": f"expected a change to {must_change}", "files": out.files})
    return Score(1.0, True, {"files": out.files, "tests_passed": True})


def _score_spec_quality(case: EvalCase, out: AgentOutput) -> Score:
    """Programmatic floor for spec quality: header, enough stories, required tags covered."""
    text = out.text or ""
    programmatic = case.expected.get("programmatic", {})
    min_stories = int(programmatic.get("minStories", 3))
    must_cover = [str(tag).lower() for tag in programmatic.get("mustCoverTags", [])]

    stories = len(_STORY_LINE.findall(text))
    tags_hit = [tag for tag in must_cover if tag in text.lower()]
    checks = {
        "has_spec_header": bool(_SPEC_HEADER.search(text)),
        "enough_stories": stories >= min_stories,
        "covers_all_tags": len(tags_hit) == len(must_cover),
    }
    value = sum(1 for ok in checks.values() if ok) / len(checks)
    return Score(
        value, value >= 0.999,
        {"stories": stories, "tags_hit": tags_hit, "tags_required": must_cover, **checks},
    )
