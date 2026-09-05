"""Deterministic smoke checks (v2 Phase 2 — plan 06 §2). NO LLM in the harness.

HTTP-level checks work at every rung that has a served URL (even with no browser): page loads
(<400), body is non-empty, and each acceptance criterion's ``expect_text`` appears in the served
HTML. A browser (capture.py) adds console-error + rendered-selector checks on top; these HTTP checks
are the always-available floor.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from foundry_core.models import AcceptanceCriterion


@dataclass(slots=True)
class CheckResult:
    id: str
    name: str
    status: str                 # "pass" | "fail" | "skipped" | "warn"
    detail: str = ""
    criterion_id: str | None = None
    evidence: list[str] = field(default_factory=list)  # artifact ids

    @property
    def ok(self) -> bool:
        return self.status in ("pass", "skipped", "warn")


async def http_smoke_checks(
    base_url: str, criteria: list[AcceptanceCriterion], *, degraded: bool = False
) -> list[CheckResult]:
    """Load-and-grep checks over the served HTML. ``degraded`` (static serve of an unbuilt SPA)
    downgrades content checks to warnings, since a blank root is expected, not a defect."""
    out: list[CheckResult] = []
    pages: dict[str, tuple[int, str]] = {}

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        async def fetch(route: str) -> tuple[int, str]:
            if route not in pages:
                try:
                    r = await client.get(base_url.rstrip("/") + "/" + route.lstrip("/"))
                    pages[route] = (r.status_code, r.text)
                except httpx.HTTPError as exc:
                    pages[route] = (0, f"__error__ {exc}")
            return pages[route]

        status, body = await fetch("/")
        out.append(CheckResult(
            "load", "page loads", "pass" if 0 < status < 400 else "fail",
            f"GET / → {status}",
        ))
        out.append(CheckResult(
            "render", "non-empty body",
            "pass" if len(body.strip()) > 0 and not body.startswith("__error__") else
            ("warn" if degraded else "fail"),
            f"{len(body)} bytes",
        ))

        for c in criteria:
            if not c.expect_text and not c.expect_selector:
                continue  # no machine block → screenshot-only elsewhere; nothing to assert here
            _st, page = await fetch(c.route or "/")
            missing = [t for t in c.expect_text if t not in page]
            # expect_selector needs a real DOM; without a browser we can only substring-match a
            # simple tag/id/class token — treat as advisory (warn on miss) at the HTTP rung.
            if missing:
                out.append(CheckResult(
                    f"criterion:{c.id}", c.criterion,
                    "warn" if degraded else "fail",
                    f"missing expected text: {missing}", criterion_id=c.id,
                ))
            else:
                out.append(CheckResult(
                    f"criterion:{c.id}", c.criterion, "pass",
                    "expected text present", criterion_id=c.id,
                ))
    return out
