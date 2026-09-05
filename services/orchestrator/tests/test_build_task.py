"""The build-task prompt: a reopened project (with a change request) must be edited in place,
focused on the LATEST change — not rebuilt from scratch from the accumulated brief.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.devloop import _build_task


def _mission(**kw):
    base = {"title": "TODO app", "requirements": "", "summary": ""}
    base.update(kw)
    return SimpleNamespace(**base)


def test_greenfield_build_task_creates_everything() -> None:
    task = _build_task(_mission(requirements="Build a simple TODO web app."))
    assert "Build this project" in task
    assert "Create every file" in task


def test_reopened_change_request_focuses_on_latest_change_in_place() -> None:
    reqs = ("Build a simple TODO web app.\n\n"
            "Change request:\nPush to a new branch.\n\n"
            "Change request:\nModify the README with tech stack and usage.")
    task = _build_task(_mission(requirements=reqs))
    assert "ALREADY EXISTS" in task
    assert "Modify the README with tech stack and usage" in task  # the LATEST change
    assert "Push to a new branch" not in task                      # not the stale one
    assert "Create every file" not in task                         # not a greenfield rebuild
