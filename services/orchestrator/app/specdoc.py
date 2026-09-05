"""Spec machine blocks (v2 Phase 0 — plan 06 §2).

The PM's spec gains a machine-checkable block per acceptance criterion, written AT SPEC
TIME so the QA harness can execute deterministic checks with no LLM in the loop:

    ```json foundry-criteria
    [{"id": "AC1", "criterion": "User can add a task", "route": "/",
      "expect_text": ["Add task"], "expect_selector": ["form"]}]
    ```

``extract_criteria`` is tolerant of real model output (fence variants, prose around the
block, single-object instead of list) and FAIL-SOFT: anything unparseable yields [] —
criteria without machine blocks degrade to screenshot-only evidence, never an error.
"""

from __future__ import annotations

import json
import re

from foundry_core.models import AcceptanceCriterion

# Appended to the PM's spec prompt by whichever engine runs the spec phase.
CRITERIA_ASK = (
    "\n\nEnd the spec with a machine-checkable block for the acceptance criteria — a fenced "
    "```json foundry-criteria code block containing a JSON array; one object per criterion: "
    '{"id": "AC1", "criterion": "<the sentence>", "route": "/<page it applies to>", '
    '"expect_text": ["<visible text that proves it>"], "expect_selector": ["<css selector>"], '
    '"severity": "blocking"|"non_blocking"}. '
    "Only include route/expect_text/expect_selector when they are genuinely checkable in a browser; "
    "otherwise give just id + criterion. Mark a criterion \"non_blocking\" ONLY when the app is fully "
    "usable without it (a nice-to-have); core criteria are \"blocking\" and should carry expect_text/"
    "expect_selector so QA can machine-check them."
)

_FENCE_RE = re.compile(
    r"```(?:json)?[ \t]*(?:foundry-criteria)?[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE
)


def _json_candidates(text: str) -> list[str]:
    """Fenced blocks first (most reliable), then any bare top-level JSON array in the text."""
    out = [m.group(1) for m in _FENCE_RE.finditer(text or "")]
    # bare-array fallback: bracket-matched scan tolerant of strings/escapes
    depth, start, in_str, esc = 0, -1, False, False
    for i, ch in enumerate(text or ""):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    out.append(text[start : i + 1])
                    start = -1
    return out


def extract_criteria(spec_text: str) -> list[AcceptanceCriterion]:
    """Pull the acceptance-criteria machine blocks out of a spec reply. [] on any failure."""
    for cand in _json_candidates(spec_text):
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            continue
        crits: list[AcceptanceCriterion] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict) or not str(item.get("criterion", "")).strip():
                continue
            crits.append(
                AcceptanceCriterion(
                    id=str(item.get("id") or f"AC{i + 1}"),
                    criterion=str(item["criterion"]).strip(),
                    route=(str(item["route"]).strip() or None) if item.get("route") else None,
                    expect_text=[str(t) for t in (item.get("expect_text") or []) if str(t).strip()],
                    expect_selector=[
                        str(s) for s in (item.get("expect_selector") or []) if str(s).strip()
                    ],
                    severity=("non_blocking" if str(item.get("severity", "")).strip().lower()
                              in ("non_blocking", "non-blocking", "minor", "nice-to-have")
                              else "blocking"),
                )
            )
        if crits:  # first candidate that yields real criteria wins
            return crits
    return []
