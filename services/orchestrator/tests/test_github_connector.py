"""GitHub connector push rules.

The product rule: when a push to the CHOSEN branch is rejected (the remote has newer commits),
DevOps must NEVER autonomously switch to a different branch or force-push. It surfaces the decision
to the user. Force-push happens only when the user authorized it, and targets the chosen branch.
"""

from __future__ import annotations

import pytest
from app.connectors.github import GitHubConnector, PushRejected


async def test_non_ff_without_force_raises_push_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = GitHubConnector(token="t", repo="owner/repo", base="main")

    async def fake_push(cwd, remote, refspec, *, force=False):  # noqa: ANN001, ANN202
        return 1, "! [rejected] main -> main (fetch first)\nUpdates were rejected"

    monkeypatch.setattr(conn, "_git_push", fake_push)
    with pytest.raises(PushRejected) as ei:
        await conn.open_pull_request(sandbox_dir="/tmp/x", branch="main", title="t", body="b")
    # It must NOT have invented a foundry/… branch; the rejected branch is reported as-is.
    assert ei.value.branch == "main"


async def test_non_ff_with_force_pushes_the_chosen_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = GitHubConnector(token="t", repo="owner/repo", base="main", force=True)
    calls: list[tuple[str, bool]] = []

    async def fake_push(cwd, remote, refspec, *, force=False):  # noqa: ANN001, ANN202
        calls.append((refspec, force))
        if not force:
            return 1, "rejected (non-fast-forward)"
        return 0, ""

    monkeypatch.setattr(conn, "_git_push", fake_push)
    res = await conn.open_pull_request(sandbox_dir="/tmp/x", branch="feature/login", title="t", body="b")
    # Force-push targets the branch the USER chose, never `base` or a fresh branch.
    assert res.branch == "feature/login"
    assert res.state == "pushed"
    assert ("HEAD:feature/login", True) in calls


async def test_clean_push_opens_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = GitHubConnector(token="t", repo="owner/repo", base="main")

    async def ok_push(cwd, remote, refspec, *, force=False):  # noqa: ANN001, ANN202
        return 0, ""

    monkeypatch.setattr(conn, "_git_push", ok_push)

    class _Resp:
        status_code = 201

        @staticmethod
        def json() -> dict:
            return {"html_url": "https://github.com/owner/repo/pull/1", "number": 1}

    class _Client:
        async def __aenter__(self):  # noqa: ANN001, ANN204
            return self

        async def __aexit__(self, *a):  # noqa: ANN001, ANN204
            return False

        async def post(self, *a, **k):  # noqa: ANN001, ANN202
            return _Resp()

    import app.connectors.github as gh

    monkeypatch.setattr(gh.httpx, "AsyncClient", lambda *a, **k: _Client())
    res = await conn.open_pull_request(sandbox_dir="/tmp/x", branch="feature/x", title="t", body="b")
    assert res.branch == "feature/x" and res.number == 1 and not res.dry_run
