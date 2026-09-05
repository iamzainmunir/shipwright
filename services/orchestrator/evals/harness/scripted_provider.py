"""ScriptedProvider — the DETERMINISTIC provider for the eval + test harness ONLY.

This is testing infrastructure, not product code: the shipped app never falls back to a fake
model (see ``app.providers.NoModelProvider``). It lets the eval suites and the automation tests
drive the whole pipeline — including the build↔QA loop, review→CTO escalation, and the loop caps
— offline and reproducibly, with no real model or network.

Output is shaped by ``purpose`` so a run looks real (a spec, a diff, a review) and metering is
non-zero. Decision phases end with a parseable ``VERDICT:`` line; the verdicts are configurable so
a test can exercise any path through the graph.
"""

from __future__ import annotations

from typing import Any

from app.providers.base import AgentTurn, LLMResult, ToolCall, ToolSpec

# The change the offline "agent" applies via fs_write (a real model generates its own).
_AGENT_FIX = '''"""Record read — scoped to the caller's tenant."""

_DB = {
    "r1": {"id": "r1", "workspace": "A", "amount": 100},
    "r2": {"id": "r2", "workspace": "B", "amount": 200},
}


def get_record(record_id, ctx):
    row = _DB.get(record_id)
    if row is None or row["workspace"] != ctx["workspace"]:
        return None
    return row
'''


def _count_tool_results(messages: list[dict[str, Any]]) -> int:
    n = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            n += sum(1 for b in content if isinstance(b, dict) and b.get("type") == "tool_result")
    return n


def _first_user_text(messages: list[dict[str, Any]]) -> str:
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"]
    return ""


def _has_attempt(prompt: str) -> bool:
    return "Attempt:" in prompt


class ScriptedProvider:
    name = "scripted"
    model = "scripted-1"

    def __init__(
        self, *, qa_fails_first: bool = False, qa_always_fail: bool = False,
        review: str = "approve", cto: str = "proceed",
    ) -> None:
        # Verdict controls (see module docstring). Defaults = a clean forward run.
        self.qa_fails_first = qa_fails_first
        self.qa_always_fail = qa_always_fail
        self.review = review
        self.cto = cto

    async def complete(
        self, *, system: str, prompt: str, purpose: str = "", max_tokens: int = 1024
    ) -> LLMResult:
        text = self._render(purpose, prompt)
        return LLMResult(
            text=text, model=self.model,
            tokens_in=max(1, (len(system) + len(prompt)) // 4),
            tokens_out=max(1, len(text) // 4), cost_cents=0,
        )

    async def complete_tools(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec],
        max_tokens: int = 2048, tool_choice: str = "auto",
    ) -> AgentTurn:
        """Deterministic tool plan — two modes:
        * greenfield build (BUILD_SYSTEM) → scaffold a minimal real starter project;
        * legacy demo → read → write the tenant fix → run tests (used by the sandbox/agent tests).
        """
        step = _count_tool_results(messages)
        if "building a real project" in (system or ""):
            return _build_plan(step, _first_user_text(messages))
        if step == 0:
            return AgentTurn("Reading the handler to find the cross-tenant read.",
                             [ToolCall("t1", "fs_read", {"path": "src/handler.py"})], 40, 18, 0)
        if step == 1:
            return AgentTurn("Adding a tenant check so a caller only sees their own workspace.",
                             [ToolCall("t2", "fs_write", {"path": "src/handler.py", "content": _AGENT_FIX})],
                             60, max(1, len(_AGENT_FIX) // 4), 0)
        if step == 2:
            return AgentTurn("Running the acceptance test.",
                             [ToolCall("t3", "cmd_run", {"command": ["python3", "tests/run_tests.py"]})],
                             30, 8, 0)
        return AgentTurn("Tenant scoping added; the cross-tenant test passes.", [], 20, 14, 0)

    def _render(self, purpose: str, prompt: str) -> str:
        t = _title(prompt)
        if purpose == "clarify":
            return "NONE"  # never block the harness on clarifying questions
        if purpose == "intake":
            return (
                f"Scoped the request: {t}.\n"
                "- The brief is clear enough to proceed.\n"
                "- Any assumptions will be captured in the spec."
            )
        if purpose == "spec":
            return (
                f"# Spec — {t}\n\n"
                "## Context\n"
                f"{_brief(prompt)}\n\n"
                "## Acceptance criteria\n"
                f"1. The primary flow described in the brief works end to end: {t}.\n"
                "2. Edge cases and error states called out in the brief are handled.\n"
                "3. The result is verifiable and documented in the README."
            )
        if purpose == "code":
            return (
                f"Implemented the core of: {t}. Created the project files and a README with run "
                "instructions."
            )
        if purpose == "qa":
            fail = self.qa_always_fail or (self.qa_fails_first and not _has_attempt(prompt))
            if fail:
                return (
                    f"Checked the build for {t}: a required acceptance criterion is not yet met.\n"
                    "VERDICT: REWORK — the primary flow from the brief is incomplete"
                )
            return (
                f"Re-checked the build for {t}: the acceptance criteria hold.\n"
                "VERDICT: PASS"
            )
        if purpose == "review":
            return {
                "approve": f"Reviewed the build for {t}. Structure is sound and documented.\nVERDICT: APPROVE",
                "rework": f"Reviewed {t}. Needs changes before merge.\nVERDICT: REWORK — tighten error handling",
                "escalate": f"Reviewed {t}. A direction call is needed.\nVERDICT: ESCALATE — architecture decision",
            }.get(self.review, "VERDICT: APPROVE")
        if purpose == "decision":
            if self.cto == "redesign_once":
                return ("VERDICT: PROCEED — approach is fine now" if _has_attempt(prompt)
                        else "VERDICT: REDESIGN — rethink the data model")
            if self.cto == "rebuild_once":
                return ("VERDICT: PROCEED — build looks right now" if _has_attempt(prompt)
                        else "VERDICT: REBUILD — reimplement the core")
            return "VERDICT: PROCEED — approach is sound; continue to the merge gate"
        return f"Done: {t}"


def _build_title(task: str) -> str:
    first = task.strip().splitlines()[0] if task.strip() else "New Project"
    return first.replace("Build this project:", "").strip()[:80] or "New Project"


def _build_plan(step: int, task: str) -> AgentTurn:
    """Offline greenfield scaffold: list → README → starter page → done."""
    title = _build_title(task)
    if step == 0:
        return AgentTurn("Scanning the empty project.",
                         [ToolCall("b1", "fs_list", {"path": ""})], 30, 6, 0)
    if step == 1:
        readme = (
            f"# {title}\n\n"
            "Starter scaffold generated by the eval harness.\n\n"
            "## Run\n- Open `index.html` in a browser to see the starter page.\n"
        )
        return AgentTurn("Writing the README with the plan and run steps.",
                         [ToolCall("b2", "fs_write", {"path": "README.md", "content": readme})],
                         40, len(readme) // 4, 0)
    if step == 2:
        index = (
            "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            f"<title>{title}</title>\n</head>\n<body>\n"
            f"  <h1>{title}</h1>\n  <p>Starter scaffold.</p>\n"
            "</body>\n</html>\n"
        )
        return AgentTurn("Adding a minimal starter page.",
                         [ToolCall("b3", "fs_write", {"path": "index.html", "content": index})],
                         40, len(index) // 4, 0)
    return AgentTurn(f"Scaffolded a starter for '{title}' (README + index.html).", [], 20, 24, 0)


def _title(prompt: str) -> str:
    first = prompt.strip().splitlines()[0] if prompt.strip() else "the change"
    return first[:120]


def _brief(prompt: str) -> str:
    """The ticket's Summary/Requirements text (between the title and the Phase line)."""
    lines = prompt.splitlines()
    body: list[str] = []
    for ln in lines[1:]:
        if ln.strip().startswith("Phase:"):
            break
        body.append(ln)
    return "\n".join(body).strip() or "(no additional detail provided)"
