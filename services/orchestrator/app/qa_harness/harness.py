"""QA evidence harness orchestrator (v2 Phase 2 — plan 06 §1, §6).

``run(...)`` = probe → serve → checks → capture → persist, wrapped in the degradation ladder
(L0 full → L4 static-file checks) and a HARD wall-clock budget. It NEVER raises into the run and
NEVER fails a run: evidence failure degrades and is recorded, per the verdict-fail-safe invariant.
The returned :class:`QaEvidence` is INPUT to the QA verdict (grounding), never authoritative.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from foundry_core.enums import ArtifactKind
from foundry_core.models import AcceptanceCriterion, Artifact

from ..artifacts import make_artifact_record, run_artifact_dir
from . import capture as cap_mod
from .checks import CheckResult, http_smoke_checks
from .detect import WebKind, probe
from .serve import serve


@dataclass(slots=True)
class QaEvidence:
    rung: str                       # "L0".."L4" — the highest rung that ran
    evidence_quality: str           # real | static | degraded_static | http_only | unserved | none
    checks: list[CheckResult] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    degradation: list[str] = field(default_factory=list)  # why each fall happened
    console_errors: list[str] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        """True only if every check passed (or was skipped/warned). Advisory input to the QA
        verdict — never authoritative, per the verdict-fail-safe invariant."""
        return all(c.ok for c in self.checks) if self.checks else False

    @property
    def summary(self) -> str:
        passed = sum(1 for c in self.checks if c.status == "pass")
        failed = sum(1 for c in self.checks if c.status == "fail")
        warned = sum(1 for c in self.checks if c.status == "warn")
        return (f"rung={self.rung} quality={self.evidence_quality} "
                f"pass={passed} fail={failed} warn={warned} artifacts={len(self.artifacts)}")


async def run(
    *, workspace_id: str, mission_id: str, run_id: str, project_path: str,
    criteria: list[AcceptanceCriterion] | None = None, step_id: str | None = None,
    total_timeout_s: int = 180, video: str = "off", allow_npm: bool = True,
    npm_timeout_s: int = 120, serve_ready_timeout_s: int = 15,
) -> QaEvidence:
    """Run the harness. Returns QaEvidence at whatever rung succeeded; never raises."""
    criteria = criteria or []
    try:
        return await asyncio.wait_for(
            _run_inner(workspace_id, mission_id, run_id, project_path, criteria, step_id,
                       video, allow_npm, npm_timeout_s, serve_ready_timeout_s),
            timeout=total_timeout_s,
        )
    except TimeoutError:
        ev = QaEvidence("L4", "none", degradation=[f"harness exceeded {total_timeout_s}s wall-clock"])
        ev.checks = [CheckResult("timeout", "QA harness timed out", "warn")]
        return ev
    except Exception as exc:  # noqa: BLE001 — the harness must never fail the run
        return QaEvidence("L4", "none", degradation=[f"harness error: {str(exc)[:160]}"],
                          checks=[CheckResult("harness", "QA harness error", "warn", str(exc)[:160])])


async def _run_inner(
    workspace_id, mission_id, run_id, project_path, criteria, step_id,
    video, allow_npm, npm_timeout_s, serve_ready_timeout_s,
) -> QaEvidence:
    pr = probe(project_path)
    out_dir = run_artifact_dir(mission_id, run_id, "qa")

    # NON_WEB → text evidence (test output / logs); no serve, no browser.
    if pr.kind is WebKind.NON_WEB:
        return await _non_web(workspace_id, mission_id, run_id, project_path, out_dir, step_id)

    ev = QaEvidence("L0", "real")
    async with serve(pr, ready_timeout_s=serve_ready_timeout_s, allow_npm=allow_npm,
                     npm_timeout_s=npm_timeout_s) as app:
        if app.base_url is None:  # L4 — could not serve → static file checks
            ev.rung, ev.evidence_quality = "L4", "unserved"
            if app.detail:
                ev.degradation.append(app.detail)
            ev.checks = _static_file_checks(pr, criteria)
            return ev

        degraded = app.quality == "degraded_static"
        if degraded:
            ev.evidence_quality = "degraded_static"
            ev.degradation.append("served the raw tree (a real build was not possible)")

        # HTTP smoke checks always run (the floor).
        ev.checks = await http_smoke_checks(app.base_url, criteria, degraded=degraded)

        # Try to add browser evidence on top (L0/L1); degrade to http_only (L3) if unavailable.
        ok, why = await cap_mod.browser_available()
        if not ok:
            ev.rung, ev.evidence_quality = "L3", ev.evidence_quality if degraded else "http_only"
            ev.degradation.append(f"visual evidence unavailable: {why}")
            return ev

        routes = _routes_for(criteria)
        want_video = video in ("on_web", "on_failure")
        capture = await cap_mod.capture_screens(
            app.base_url, routes, out_dir, record_video=want_video, video_max_seconds=30,
        )
        ev.console_errors = capture.console_errors
        if not capture.ok:
            ev.rung = "L3"
            ev.degradation.append(f"visual evidence unavailable: {capture.detail}")
            return ev

        # Console-error check (only meaningful with a real bundle; a warning on degraded serves).
        ev.checks.append(CheckResult(
            "console", "no console errors",
            "pass" if not capture.console_errors else ("warn" if degraded else "fail"),
            f"{len(capture.console_errors)} error(s)",
        ))
        # Persist screenshots as artifacts.
        for meta in capture.screenshot_meta:
            art = make_artifact_record(
                workspace_id=workspace_id, mission_id=mission_id, run_id=run_id, step_id=step_id,
                file_path=meta["path"], kind=ArtifactKind.SCREENSHOT,
                meta={"criterion_id": meta["criterion_id"], "route": meta["route"], "seq": meta["seq"],
                      "evidence_quality": ev.evidence_quality},
            )
            ev.artifacts.append(art)
        if capture.video and capture.video.is_file():
            ev.artifacts.append(make_artifact_record(
                workspace_id=workspace_id, mission_id=mission_id, run_id=run_id, step_id=step_id,
                file_path=capture.video, kind=ArtifactKind.VIDEO, meta={"evidence_quality": ev.evidence_quality},
            ))
        ev.rung = "L1" if want_video and not capture.video else "L0"
    return ev


def _routes_for(criteria: list[AcceptanceCriterion]) -> list[tuple[str, str | None]]:
    routes: list[tuple[str, str | None]] = [("/", None)]
    seen = {"/"}
    for c in criteria:
        r = c.route or "/"
        key = f"{r}:{c.id}"
        if key not in seen:
            seen.add(key)
            routes.append((r, c.id))
    return routes[:12]  # qa.max_screens


def _static_file_checks(pr, criteria: list[AcceptanceCriterion]) -> list[CheckResult]:
    """L4: no server — check the files on disk exist and contain expected text."""
    root = Path(pr.project_path)
    out = [CheckResult("index", "an index.html exists",
                       "pass" if pr.index_html else "fail",
                       pr.index_html or "no index.html found")]
    if pr.index_html:
        html = (root / pr.index_html).read_text(errors="replace")
        for c in criteria:
            if not c.expect_text:
                continue
            missing = [t for t in c.expect_text if t not in html]
            out.append(CheckResult(f"criterion:{c.id}", c.criterion,
                                   "warn" if missing else "pass",
                                   f"missing: {missing}" if missing else "text present",
                                   criterion_id=c.id))
    return out


async def _non_web(workspace_id, mission_id, run_id, project_path, out_dir, step_id) -> QaEvidence:
    """Text-evidence path for CLI/python projects: capture the test command output as a LOG artifact."""
    from ..sandbox import LocalSandbox
    ev = QaEvidence("L0", "none")
    log = ""
    try:
        sb = await LocalSandbox.at_path(project_path)
        if (Path(project_path) / "tests").is_dir() or (Path(project_path) / "pyproject.toml").is_file():
            res = await sb.run("python3", "-m", "pytest", "-q", timeout_s=120)
            log = (res.stdout + res.stderr)
            ev.checks = [CheckResult("tests", "test suite", "pass" if res.code == 0 else "fail",
                                     f"exit={res.code}")]
        else:
            ev.checks = [CheckResult("nonweb", "no runnable test suite", "skipped")]
    except Exception as exc:  # noqa: BLE001
        ev.degradation.append(f"non-web check error: {str(exc)[:160]}")
        ev.checks = [CheckResult("nonweb", "non-web check", "warn", str(exc)[:120])]
    if log:
        head_tail = log[:100_000] + ("\n… (truncated) …\n" + log[-400_000:] if len(log) > 524_288 else "")
        p = out_dir / "test_output.log"
        p.write_text(head_tail)
        ev.artifacts.append(make_artifact_record(
            workspace_id=workspace_id, mission_id=mission_id, run_id=run_id, step_id=step_id,
            file_path=p, kind=ArtifactKind.LOG, meta={"evidence_quality": "none"}))
    return ev
