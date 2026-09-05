"""Render a SuiteResult as Markdown (PR comment) and JUnit XML (CI test report)."""

from __future__ import annotations

from xml.sax.saxutils import escape

from .models import SuiteResult


def to_markdown(run: SuiteResult) -> str:
    verdict = "✅ PASS" if run.passed else "❌ FAIL"
    lines = [
        f"### Eval `{run.suite_id}` v{run.version} — {verdict}",
        "",
        f"- mode: `{run.mode}` · samples/case: {run.samples}",
        f"- suite mean: **{run.mean_score:.3f}** (gate ≥ {run.gate_min:.2f})",
        "",
        "| case | mean | pass-rate | cost¢ p50 | budget |",
        "|---|---|---|---|---|",
    ]
    for case in run.cases:
        budget = "ok" if case.budget_ok else "OVER"
        lines.append(
            f"| {case.case_id} | {case.mean_score:.3f} | {case.pass_rate:.0%} "
            f"| {case.cost_cents_p50} | {budget} |"
        )
    return "\n".join(lines) + "\n"


def to_junit(run: SuiteResult) -> str:
    """One <testcase> per eval case; a failing gate marks its cases as failed."""
    failures = 0 if run.passed else sum(1 for c in run.cases if c.mean_score < run.gate_min)
    cases_xml = []
    for case in run.cases:
        name = escape(case.case_id)
        body = ""
        if case.mean_score < run.gate_min:
            reason = escape(f"mean {case.mean_score:.3f} < gate {run.gate_min:.2f}")
            body = f'<failure message="{reason}"/>'
        cases_xml.append(
            f'<testcase classname="{escape(run.suite_id)}" name="{name}">{body}</testcase>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<testsuite name="eval:{escape(run.suite_id)}" tests="{len(run.cases)}" '
        f'failures="{failures}">\n  ' + "\n  ".join(cases_xml) + "\n</testsuite>\n"
    )
