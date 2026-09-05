"""GitHub connector (doc 07) — open a pull request for a mission's branch.

Two modes, chosen by configuration:
  * **live** — when a token AND repo are configured: push the sandbox branch to the remote and
    open a real PR via the GitHub REST API. This is an outward-facing action, so callers only
    invoke it after the mission's **merge approval gate** is satisfied.
  * **dry-run** (default here) — no token/repo: no network call; returns a synthetic PR result
    so the whole flow runs safely end-to-end locally.

Credentials come from the environment (Vault-backed in production, Canon §13.7); a token is
never logged or persisted.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

_API = "https://api.github.com"


@dataclass(slots=True)
class PushRecovery:
    """What DevOps did to get the work pushed when the direct push was rejected."""

    branch: str          # the branch actually pushed
    note: str            # a human-readable explanation of the recovery


class PushRejected(RuntimeError):
    """A non-fast-forward push to the CHOSEN branch was rejected (the remote has newer commits).

    How to resolve it — force-push to overwrite the branch, or push to a DIFFERENT branch — is a
    USER decision and is NEVER taken autonomously. Neither the AI nor the CTO may force-push or
    switch branches on their own; the engine surfaces this back to the user at the merge gate.
    """

    def __init__(self, branch: str, detail: str = "") -> None:
        self.branch = branch
        self.detail = detail
        super().__init__(
            f"Push to '{branch}' was rejected — the remote has newer commits. "
            "It needs your decision: authorize a force-push to overwrite it, or choose a different "
            f"branch. {detail}".strip()
        )


@dataclass(slots=True)
class PullRequestResult:
    url: str
    number: int
    branch: str
    base: str
    dry_run: bool
    state: str = "open"
    recovery: str = ""  # set when DevOps had to recover from a push error (e.g. diverged main)


class GitHubConnector:
    def __init__(self, token: str = "", repo: str = "", base: str = "main", *, force: bool = False) -> None:
        self.token = token.strip()
        self.repo = repo.strip()  # "owner/name"
        self.base = base
        # When true, a non-fast-forward rejection is resolved by FORCE-pushing the base branch
        # (overwrites the remote history) instead of opening a PR from a new branch. Opt-in only —
        # destructive, so it defaults off and is meant for throwaway/test repos.
        self.force = force
        self.live = bool(self.token and self.repo)

    async def open_pull_request(
        self, *, sandbox_dir: str, branch: str, title: str, body: str
    ) -> PullRequestResult:
        if not self.live:
            # Safe local dry-run — no network, no external side effects.
            repo = self.repo or "your-org/your-repo"
            return PullRequestResult(
                url=f"https://github.com/{repo}/pull/DRY-RUN-{branch}",
                number=0, branch=branch, base=self.base, dry_run=True,
            )
        # Live: push the branch, then open the PR.
        remote = f"https://x-access-token:{self.token}@github.com/{self.repo}.git"
        # Push the current HEAD onto the target branch (HEAD:{branch}), not {branch}:{branch}: this
        # creates/updates the remote branch from whatever is checked out, so an ops task ("push the
        # existing code to a new branch") works even when no local branch named `branch` was created.
        code, err = await self._git_push(sandbox_dir, remote, f"HEAD:{branch}")

        recovery = ""
        push_branch = branch
        if code != 0:
            lowered = err.lower()
            non_ff = any(s in lowered for s in
                         ("fetch first", "rejected", "non-fast-forward", "failed to push some refs"))
            if not non_ff:
                raise RuntimeError(f"git push failed: {err[:200]}")
            if self.force:
                # The USER authorized a force-push at the gate. Overwrite the branch THEY chose
                # (`branch`) — never a different one — with this project's history.
                codeF, errF = await self._git_push(sandbox_dir, remote, f"HEAD:{branch}", force=True)
                if codeF != 0:
                    raise RuntimeError(f"git force-push to {branch} failed: {errF[:200]}")
                return PullRequestResult(
                    url=f"https://github.com/{self.repo}/tree/{branch}", number=0,
                    branch=branch, base=self.base, dry_run=False, state="pushed",
                    recovery=f"Push to {branch} was rejected (the remote had newer commits), so DevOps "
                             f"FORCE-pushed to {branch} (overwrote the remote) as you authorized.",
                )
            # Non-fast-forward and NO force authorization. Do NOT silently push to a different branch
            # — the target branch is the user's choice. Surface it so the USER decides (authorize a
            # force-push, or pick a different branch). Nothing is lost: the work stays in the sandbox.
            raise PushRejected(branch=branch, detail=err[:160])

        # Pushing the base branch itself with no recovery (e.g. a greenfield app's initial `main`
        # onto an empty/fast-forwardable remote) — there is no PR to open (head == base).
        if push_branch == self.base:
            return PullRequestResult(
                url=f"https://github.com/{self.repo}", number=0, branch=push_branch, base=self.base,
                dry_run=False, state="pushed",
            )

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        payload = {"title": title, "head": push_branch, "base": self.base, "body": body}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{_API}/repos/{self.repo}/pulls", headers=headers, json=payload)
        if resp.status_code >= 400:
            body_txt = resp.text.lower()
            # The work IS pushed; only PR creation failed. If the app's history is unrelated to the
            # base (a greenfield repo pushed into an existing one) or there's nothing to compare,
            # GitHub won't open a PR — that's not a delivery failure. Report the branch as pushed
            # with a note, instead of erroring, so the branch on GitHub isn't hidden behind a red X.
            unrelated = ("no history in common" in body_txt or "no commits between" in body_txt
                         or "not have any commits" in body_txt)
            if unrelated:
                note = (recovery + " " if recovery else "") + (
                    f"Pushed the branch {push_branch}, but GitHub couldn't open a PR — the app's git "
                    f"history is unrelated to {self.base}. Compare/merge it manually on GitHub.")
                return PullRequestResult(
                    url=f"https://github.com/{self.repo}/tree/{push_branch}", number=0,
                    branch=push_branch, base=self.base, dry_run=False, state="pushed",
                    recovery=note.strip(),
                )
            raise RuntimeError(f"GitHub PR failed {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        return PullRequestResult(
            url=data["html_url"], number=data["number"], branch=push_branch, base=self.base,
            dry_run=False, recovery=recovery,
        )

    def _headers(self) -> dict[str, str]:
        """Auth headers for the GitHub REST API (Bearer token)."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def list_repos(self, *, per_page: int = 100) -> list[dict]:
        """GET /user/repos — the repos this token can push to, most-recently-updated first.
        Returns a light shape: [{fullName, defaultBranch, private, pushedAt}]. Raises if no token."""
        if not self.token:
            raise RuntimeError("no GitHub token — connect GitHub first")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{_API}/user/repos",
                headers=self._headers(),
                params={"per_page": per_page, "sort": "pushed",
                        "affiliation": "owner,collaborator,organization_member"},
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"GitHub repos list failed {resp.status_code}: {resp.text[:160]}")
        return [
            {
                "fullName": r.get("full_name", ""),
                "defaultBranch": r.get("default_branch", "main"),
                "private": bool(r.get("private")),
                "pushedAt": r.get("pushed_at"),
            }
            for r in resp.json()
            if r.get("full_name")
        ]

    async def list_branches(self, owner: str, repo: str, *, per_page: int = 100) -> list[str]:
        """GET /repos/{owner}/{repo}/branches — branch names for one repo. Raises if no token."""
        if not self.token:
            raise RuntimeError("no GitHub token — connect GitHub first")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{_API}/repos/{owner}/{repo}/branches",
                headers=self._headers(),
                params={"per_page": per_page},
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"GitHub branches list failed {resp.status_code}: {resp.text[:160]}")
        return [b.get("name", "") for b in resp.json() if b.get("name")]

    async def _git_push(self, cwd: str, remote: str, refspec: str, *, force: bool = False) -> tuple[int, str]:
        """Run one `git push`; return (returncode, stderr-text). Never raises on a non-zero push."""
        args = ["git", "push"] + (["--force"] if force else []) + [remote, refspec]
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        return proc.returncode, err.decode(errors="replace")
