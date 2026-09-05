"""QA evidence harness (v2 Phase 2 — plan 06).

Deterministic, LLM-free evidence gathering: probe the built project → serve it → run smoke checks →
capture Playwright screenshots/video → persist artifacts. Wrapped in a degradation ladder (L0 full →
L4 static-file checks) and a hard wall-clock budget. It NEVER raises into a run and NEVER fails a run;
its :class:`QaEvidence` is grounding INPUT to the QA verdict, never authoritative.
"""

from __future__ import annotations

from .checks import CheckResult
from .detect import ProbeResult, WebKind, probe
from .harness import QaEvidence, run

__all__ = ["CheckResult", "ProbeResult", "QaEvidence", "WebKind", "probe", "run"]
