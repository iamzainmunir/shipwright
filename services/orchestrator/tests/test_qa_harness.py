"""Phase 2 — QA evidence harness (plan 06).

These run at the *no-browser* rung (chromium is not installed in CI here), so they assert the harness
degrades gracefully and NEVER raises / NEVER fails a run. If chromium happens to be present, the
end-to-end test still passes (it simply reaches a higher rung).
"""

from __future__ import annotations

import json
import shutil

import pytest
from app import config
from app.qa_harness import checks, detect, harness
from foundry_core.models import AcceptanceCriterion


@pytest.fixture()
def artifacts_root(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    monkeypatch.setenv("SHIPWRIGHT_ARTIFACTS_ROOT", str(root))
    config.get_settings.cache_clear()
    yield root
    config.get_settings.cache_clear()


def _static_site(tmp_path, body: str = "<h1>Hello Shipwright</h1><main>Dashboard</main>") -> str:
    d = tmp_path / "site"
    d.mkdir()
    (d / "index.html").write_text(f"<!doctype html><html><body>{body}</body></html>")
    return str(d)


def _vite_project(tmp_path) -> str:
    d = tmp_path / "app"
    d.mkdir()
    (d / "package.json").write_text(json.dumps({
        "name": "x", "dependencies": {"react": "18", "vite": "5"},
        "scripts": {"build": "vite build"},
    }))
    (d / "index.html").write_text("<!doctype html><div id='root'></div>")
    return str(d)


# ---- detect -------------------------------------------------------------------

def test_probe_static(tmp_path):
    pr = detect.probe(_static_site(tmp_path))
    assert pr.kind is detect.WebKind.STATIC_HTML
    assert pr.index_html == "index.html"
    assert pr.is_web


def test_probe_vite(tmp_path):
    pr = detect.probe(_vite_project(tmp_path))
    assert pr.kind is detect.WebKind.VITE_REACT


def test_probe_non_web(tmp_path):
    d = tmp_path / "pylib"
    d.mkdir()
    (d / "main.py").write_text("print('hi')")
    pr = detect.probe(str(d))
    assert pr.kind is detect.WebKind.NON_WEB
    assert not pr.is_web


def test_probe_missing_dir_is_non_web():
    assert detect.probe("/nope/does/not/exist").kind is detect.WebKind.NON_WEB


def test_probe_node_server_without_package_json(tmp_path):
    # A bare src/server.js with NO package.json is still a Node app to RUN — not a dead static tree.
    d = tmp_path / "bare"
    (d / "src").mkdir(parents=True)
    (d / "src" / "server.js").write_text("// a Node http server (detection only checks it exists)\n")
    (d / "public").mkdir()
    (d / "public" / "index.html").write_text("<h1>Bare</h1>")
    pr = detect.probe(str(d))
    assert pr.kind is detect.WebKind.NODE_SERVED
    assert pr.node_entry == "src/server.js"


# ---- serve + HTTP checks ------------------------------------------------------

async def test_serve_static_and_smoke_checks(tmp_path):
    from app.qa_harness.serve import serve
    pr = detect.probe(_static_site(tmp_path))
    async with serve(pr) as app:
        assert app.base_url is not None
        assert app.quality == "static"
        results = await checks.http_smoke_checks(
            app.base_url,
            [AcceptanceCriterion(id="c1", criterion="shows dashboard", route="/",
                                 expect_text=["Dashboard"])],
        )
    by_id = {r.id: r for r in results}
    assert by_id["load"].status == "pass"
    assert by_id["render"].status == "pass"
    assert by_id["criterion:c1"].status == "pass"


async def test_smoke_check_flags_missing_text(tmp_path):
    from app.qa_harness.serve import serve
    pr = detect.probe(_static_site(tmp_path))
    async with serve(pr) as app:
        results = await checks.http_smoke_checks(
            app.base_url,
            [AcceptanceCriterion(id="c9", criterion="has a login button",
                                 route="/", expect_text=["Sign in with SSO"])],
        )
    crit = next(r for r in results if r.criterion_id == "c9")
    assert crit.status == "fail"
    assert "Sign in with SSO" in crit.detail


def test_benign_console_errors_are_ignored_but_real_js_errors_are_not() -> None:
    """The classifier is URL-aware: a missing STATIC asset (favicon/icon/image) is not a code defect
    and must not fail QA, but a failed API/data fetch IS a real bug and must be kept. When no URL is
    known, err toward KEEPING the error — a bare '404' text alone could be an API failure."""
    from app.qa_harness.capture import _is_benign_console_error

    # Static-asset failures → benign (decided from the failing resource URL).
    assert _is_benign_console_error("Failed to load resource: 404", "http://localhost/favicon.ico")
    assert _is_benign_console_error("Failed to load resource: 404", "http://localhost/logo.png")
    assert _is_benign_console_error("GET http://localhost/favicon.ico 404 (Not Found)")  # favicon in text

    # API/data failures → NEVER benign, even with identical 404 text (this was the too-broad-filter bug).
    assert not _is_benign_console_error("Failed to load resource: 404", "http://localhost/api/notes")
    assert not _is_benign_console_error("Failed to load resource: 500", "http://localhost/data.json")

    # No URL + no favicon/icon phrasing → ambiguous, so KEEP it (safer than greenlighting a broken app).
    assert not _is_benign_console_error("Failed to load resource: the server responded with a status of 404")

    # Real JS errors always count, URL or not.
    assert not _is_benign_console_error("Uncaught TypeError: tasks.map is not a function")
    assert not _is_benign_console_error("ReferenceError: renderList is not defined")


def _node_app(tmp_path) -> str:
    """A minimal Node app whose ONLY run path is `npm start`/`node server.js` (no build script) — the
    exact shape that used to degrade to a dead static serve (the M-172 QA-loop bug)."""
    d = tmp_path / "nodeapp"
    d.mkdir()
    (d / "package.json").write_text(json.dumps({"name": "n", "scripts": {"start": "node server.js"}}))
    (d / "index.html").write_text("<!doctype html><html><body><h1>Node UI</h1></body></html>")
    (d / "server.js").write_text(
        "const http = require('http');\n"
        "const fs = require('fs');\n"
        "const path = require('path');\n"
        "const port = Number(process.env.PORT || 3000);\n"
        "http.createServer((req, res) => {\n"
        "  if (req.url === '/api/notes') {\n"
        "    res.writeHead(200, {'content-type': 'application/json'});\n"
        "    res.end(JSON.stringify([{id: 1}]));\n"
        "    return;\n"
        "  }\n"
        "  res.writeHead(200, {'content-type': 'text/html'});\n"
        "  res.end(fs.readFileSync(path.join(__dirname, 'index.html')));\n"
        "}).listen(port, '127.0.0.1');\n"
    )
    return str(d)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
async def test_preview_manager_runs_the_real_app_then_stops(tmp_path):
    """The 'Run app' preview boots the app's own server, serves the real API, and stops cleanly."""
    import httpx
    from app.preview import PreviewManager
    pm = PreviewManager(ready_timeout_s=20)
    info = await pm.start("m-preview", _node_app(tmp_path))
    try:
        assert info["running"] and info["kind"] == "node"
        st = pm.status("m-preview")
        assert st and st["running"] and st["url"] == info["url"]
        async with httpx.AsyncClient(timeout=5) as c:
            api = await c.get(info["url"] + "/api/notes")
        assert api.status_code == 200 and api.json() == [{"id": 1}]  # the REAL API, not a static 404
    finally:
        assert await pm.stop("m-preview") is True
    assert pm.status("m-preview") is None  # gone after stop


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
async def test_serve_node_runs_the_real_server_not_a_static_fallback(tmp_path):
    """The harness must BOOT the app's own server so QA sees the real API — not static-serve a tree
    where every /api route 404s (which made backend/full-stack apps look broken and loop forever)."""
    import httpx
    from app.qa_harness.serve import serve
    pr = detect.probe(_node_app(tmp_path))
    assert pr.kind is detect.WebKind.NODE_SERVED
    async with serve(pr, ready_timeout_s=20) as app:
        assert app.base_url is not None
        assert app.quality == "real", app.detail  # ran `node server.js`, did NOT fall back to static
        async with httpx.AsyncClient(timeout=5) as c:
            page = await c.get(app.base_url + "/")
            api = await c.get(app.base_url + "/api/notes")
    assert page.status_code == 200 and "Node UI" in page.text
    assert api.status_code == 200 and api.json() == [{"id": 1}]  # the REAL API responded


# ---- harness end-to-end -------------------------------------------------------

async def test_harness_static_end_to_end(tmp_path, artifacts_root):
    ev = await harness.run(
        workspace_id="ws1", mission_id="m1", run_id="r1",
        project_path=_static_site(tmp_path),
        criteria=[AcceptanceCriterion(id="c1", criterion="shows dashboard",
                                      route="/", expect_text=["Dashboard"])],
    )
    # Rung depends on whether chromium is installed; either way the run succeeds and checks pass.
    assert ev.rung in ("L0", "L1", "L3")
    assert ev.evidence_quality in ("real", "http_only")
    assert ev.all_ok, ev.summary
    load = next(c for c in ev.checks if c.id == "load")
    assert load.status == "pass"


async def test_harness_never_raises_on_bad_path(artifacts_root):
    ev = await harness.run(
        workspace_id="ws1", mission_id="m1", run_id="rx",
        project_path="/definitely/not/here",
    )
    assert isinstance(ev, harness.QaEvidence)
    assert ev.rung in ("L0", "L4")  # non-web text path or unserved


async def test_harness_non_web_text_evidence(tmp_path, artifacts_root):
    d = tmp_path / "lib"
    d.mkdir()
    (d / "main.py").write_text("print('hi')\n")
    ev = await harness.run(
        workspace_id="ws1", mission_id="m2", run_id="r2", project_path=str(d),
    )
    assert ev.rung == "L0"
    assert ev.evidence_quality == "none"
    # no pyproject/tests dir → the check is skipped, not failed
    assert any(c.id == "nonweb" for c in ev.checks)


async def test_harness_degraded_static_warns_not_fails(tmp_path, artifacts_root):
    """A vite project with no node_modules and npm blocked → served raw → content checks WARN."""
    ev = await harness.run(
        workspace_id="ws1", mission_id="m3", run_id="r3",
        project_path=_vite_project(tmp_path),
        criteria=[AcceptanceCriterion(id="c1", criterion="renders app",
                                      route="/", expect_text=["This text is not in the raw HTML"])],
        allow_npm=False,
    )
    assert ev.evidence_quality in ("degraded_static", "http_only", "unserved")
    # The whole point: a missing-text criterion on a degraded serve must NOT hard-fail.
    crit = [c for c in ev.checks if c.criterion_id == "c1"]
    if crit:
        assert crit[0].status in ("warn", "pass")
    assert ev.degradation  # something was recorded about the degrade


def test_qaevidence_properties():
    ev = harness.QaEvidence("L0", "real")
    assert ev.all_ok is False  # no checks yet
    ev.checks.append(checks.CheckResult("load", "loads", "pass"))
    assert ev.all_ok is True
    ev.checks.append(checks.CheckResult("x", "x", "fail"))
    assert ev.all_ok is False
    assert "rung=L0" in ev.summary


# ---- engine seam: evidence grounds the QA verdict ------------------------------

async def test_engine_run_qa_evidence_grounds_verdict(tmp_path, artifacts_root):
    """The build wiring: spec criteria → harness → persisted artifacts + facts → grounding line
    that the QA/review agents actually see. Proves the harness is not dead code."""
    from app.engine import RunEngine
    from app.events import EventBus
    from app.store import InMemoryStore
    from foundry_core.enums import RunStatus
    from foundry_core.ids import new_ulid
    from foundry_core.models import Event, Run

    from tests.support.scripted_provider import ScriptedProvider

    store = InMemoryStore()
    store.model_connections.clear()
    engine = RunEngine(store, EventBus(), ScriptedProvider())

    mission = await store.get_mission("FND-142")
    assert mission is not None
    site = _static_site(tmp_path, body="<h1>Dashboard</h1><main>Add task</main>")
    mission = await store.update_mission(mission.id, project_path=site)

    run = await store.add_run(Run(id=new_ulid(), mission_id=mission.id,
                                  workspace_id=mission.workspace_id, status=RunStatus.RUNNING))
    # Seed a spec output carrying a machine-checkable criteria block.
    spec = (
        "Here is the spec.\n\n```json foundry-criteria\n"
        '[{"id": "AC1", "criterion": "shows the dashboard", "route": "/", '
        '"expect_text": ["Dashboard"]}]\n```\n'
    )
    await store.add_event(Event(id=new_ulid(), run_id=run.id, mission_id=mission.id,
                                workspace_id=mission.workspace_id, type="spec", text=spec,
                                payload={"kind": "phase.output", "phase": "spec"}))

    # Criteria are parsed from the spec event.
    crit = await engine._acceptance_criteria(mission.id)
    assert [c.id for c in crit] == ["AC1"]

    await engine._run_qa_evidence(run.id, mission.id, mission)

    facts = engine._build_facts.get(mission.id) or {}
    ev = facts.get("qa_evidence")
    assert ev is not None
    assert ev["rung"] in ("L0", "L1", "L3")
    names = {c["name"]: c["status"] for c in ev["checks"]}
    assert names.get("page loads") == "pass"
    assert names.get("shows the dashboard") == "pass"  # criterion text was found in served HTML

    # The grounding line the verdict phases see must mention the evidence + a passed check.
    line = engine._qa_evidence_line(facts)
    assert "QA evidence harness" in line
    assert "page loads=pass" in line
