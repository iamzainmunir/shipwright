"""Sandbox dev loop (Phase 3) — real git branch/edit/commit/diff + PR dry-run.

Uses the actual ``git`` CLI in a temp workspace, so it exercises the real dev loop (no mocks).
"""

from __future__ import annotations

from app.connectors import GitHubConnector
from app.devloop import open_pr, real_build
from app.sandbox import LocalSandbox
from app.store import InMemoryStore

from tests.support.scripted_provider import ScriptedProvider


async def test_root_sha_reports_whole_app_not_just_the_rework_delta(tmp_path):
    """The M-172 QA-loop bug: on a rework, diffing from the current HEAD shows only that wave's tiny
    delta, so QA (grounded on the diff) thinks earlier files 'disappeared' and loops forever. Diffing
    from the repo ROOT always shows the COMPLETE app — that's the fix."""
    sb = await LocalSandbox.at_path(str(tmp_path / "app"))
    await sb.init_empty_repo()
    # Wave 1: backend + frontend land and are committed.
    await sb.write_file("src/backend/server.js", "// http server\n")
    await sb.write_file("src/frontend/index.html", "<!doctype html>\n")
    await sb.commit_all("wave 1")
    after_wave1 = await sb.head_sha()
    # Rework wave: touches ONE new file only.
    await sb.write_file("src/frontend/app.js", "// client logic\n")
    await sb.commit_all("rework")

    # OLD behaviour (base = current HEAD): QA would see only the rework's single file → 'server missing'.
    incremental = await sb.changed_files(after_wave1)
    assert incremental == ["src/frontend/app.js"]

    # FIX (base = root): the whole committed app is reported every time.
    root = await sb.root_sha()
    whole = set(await sb.changed_files(root))
    assert {"src/backend/server.js", "src/frontend/index.html", "src/frontend/app.js"} <= whole
    assert "server" in await sb.diff(root)


async def test_real_build_produces_branch_diff_and_passing_tests(tmp_path):
    store = InMemoryStore()
    mission = await store.get_mission("FND-142")
    assert mission is not None

    result, sb = await real_build(mission, ScriptedProvider(), sandbox_root=str(tmp_path))
    try:
        assert result.branch.startswith("fix/FND-142-")
        assert result.tests_passed is True, result.test_output
        assert "src/handler.py" in result.files
        assert result.diff.strip(), "expected a non-empty unified diff"
        assert "ctx[" in result.diff or "workspace" in result.diff  # the tenant guard shows up
        # the branch really exists in the repo
        cur = await sb.current_branch()
        assert cur == result.branch
    finally:
        await sb.destroy()


async def test_open_pr_dry_run_when_unconfigured(tmp_path):
    store = InMemoryStore()
    mission = await store.get_mission("FND-142")
    assert mission is not None
    result, sb = await real_build(mission, ScriptedProvider(), sandbox_root=str(tmp_path))
    try:
        pr = await open_pr(sb, mission, result.branch, GitHubConnector())  # no token → dry-run
        assert pr.dry_run is True
        assert "DRY-RUN" in pr.url
        assert pr.branch == result.branch
    finally:
        await sb.destroy()
