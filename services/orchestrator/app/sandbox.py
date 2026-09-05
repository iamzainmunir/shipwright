"""Execution sandbox (doc 08) — an isolated workspace where an agent does real dev work.

Phase 3 ships a **local** sandbox: a throwaway directory with a real git repo, driven via
subprocesses (git, tests). It gives the agent genuine branch/edit/commit/diff/test operations
so a mission produces a real change and a real unified diff.

Production isolation is a Firecracker microVM / gVisor+Docker with a locked-down egress
allowlist and resource caps (doc 08 §Sandbox); the interface here (create → run → snapshot →
destroy) is the same, so that swap is drop-in. No network egress happens here unless the
GitHub connector is explicitly configured to push.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",  # never hang on an auth prompt
    "GIT_AUTHOR_NAME": "Shipwright Agent",
    "GIT_AUTHOR_EMAIL": "agent@shipwright.local",
    "GIT_COMMITTER_NAME": "Shipwright Agent",
    "GIT_COMMITTER_EMAIL": "agent@shipwright.local",
}


@dataclass(slots=True)
class ExecResult:
    code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.code == 0


class LocalSandbox:
    """A disposable local working directory with a git repo."""

    def __init__(self, workdir: Path) -> None:
        self.dir = workdir

    @classmethod
    async def create(cls, root: str | None, name: str) -> LocalSandbox:
        base = Path(root) if root else Path(os.environ.get("TMPDIR", "/tmp")) / "foundry-sandboxes"
        workdir = base / name
        if workdir.exists():
            shutil.rmtree(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        return cls(workdir)

    @classmethod
    async def at_path(cls, path: str) -> LocalSandbox:
        """Open a persistent project directory at an absolute path WITHOUT destroying it.

        Used for greenfield app builds where the directory IS the deliverable. Parents are
        created; existing content is left untouched (the caller decides whether it's safe to build).
        """
        # Local-sandbox fs setup is intentionally synchronous, like the rest of this module.
        workdir = Path(os.path.expanduser(path))  # noqa: ASYNC240
        workdir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        return cls(workdir)

    async def is_empty(self) -> bool:
        return not any(p for p in self.dir.iterdir() if p.name != ".git")

    async def has_repo(self) -> bool:
        return (self.dir / ".git").exists()

    async def init_empty_repo(self, *, default_branch: str = "main") -> None:
        """git init with a minimal starter commit so the agent has a clean base + diff support."""
        await self.git("init", "-q", "-b", default_branch)
        # Ignore build/dependency/venv artifacts so they never pollute the committed diff. Only
        # UNAMBIGUOUS venv markers (pyvenv.cfg, named venv dirs) — never bare bin/ or lib/, which
        # real projects use for source.
        await self.write_file(".gitignore", (
            "node_modules/\n__pycache__/\n*.pyc\n.next/\ndist/\nbuild/\n.env\n"
            "venv/\n.venv/\nenv/\nENV/\npyvenv.cfg\n.DS_Store\n"
        ))
        await self.git("add", "-A")
        await self.git("commit", "-q", "-m", "chore: initialize project")

    async def run(self, *args: str, cwd: Path | None = None, timeout_s: float = 60.0,
                  env: dict[str, str] | None = None) -> ExecResult:
        full_env = {**os.environ, **_GIT_ENV, **(env or {})}
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=str(cwd or self.dir), env=full_env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            proc.kill()
            return ExecResult(124, "", f"timed out after {timeout_s}s")
        return ExecResult(proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace"))

    async def git(self, *args: str, timeout_s: float = 60.0) -> ExecResult:
        return await self.run("git", *args, timeout_s=timeout_s)

    async def write_file(self, rel: str, content: str) -> None:
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    async def read_file(self, rel: str) -> str:
        return (self.dir / rel).read_text(encoding="utf-8")

    async def list_files(self, rel: str = "", *, limit: int = 400) -> list[str]:
        """Repo-relative file paths under `rel` (recursive), skipping VCS/build noise."""
        base = (self.dir / rel).resolve()
        if not str(base).startswith(str(self.dir.resolve())) or not base.exists():
            return []
        skip = {".git", "node_modules", "__pycache__", ".next", "dist", "build", ".venv"}
        out: list[str] = []
        for path in sorted(base.rglob("*")):
            if any(part in skip for part in path.relative_to(self.dir).parts):
                continue
            if path.is_file():
                out.append(str(path.relative_to(self.dir)))
                if len(out) >= limit:
                    break
        return out

    async def init_repo(self, seed: dict[str, str], *, default_branch: str = "main") -> None:
        await self.git("init", "-q", "-b", default_branch)
        for rel, content in seed.items():
            await self.write_file(rel, content)
        await self.git("add", "-A")
        await self.git("commit", "-q", "-m", "chore: seed repository")

    async def checkout_branch(self, name: str) -> None:
        await self.git("checkout", "-q", "-b", name)

    async def commit_all(self, message: str) -> ExecResult:
        await self.git("add", "-A")
        return await self.git("commit", "-q", "-m", message)

    async def diff(self, base: str = "main") -> str:
        res = await self.git("diff", f"{base}...HEAD")
        return res.stdout

    async def changed_files(self, base: str = "main") -> list[str]:
        res = await self.git("diff", "--name-only", f"{base}...HEAD")
        return [line for line in res.stdout.splitlines() if line.strip()]

    async def current_branch(self) -> str:
        res = await self.git("rev-parse", "--abbrev-ref", "HEAD")
        return res.stdout.strip()

    async def head_sha(self) -> str:
        res = await self.git("rev-parse", "HEAD")
        return res.stdout.strip()

    async def root_sha(self) -> str:
        """The repo's initial commit. For a greenfield app the whole tree since here IS the
        deliverable, so diffing/listing from root shows the COMPLETE app — not just the latest
        rework's delta (which is what makes QA think earlier files 'disappeared')."""
        res = await self.git("rev-list", "--max-parents=0", "HEAD")
        roots = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        return roots[-1] if roots else await self.head_sha()

    # ---- git worktrees (parallel, isolated agent workspaces) --------------------
    async def add_worktree(self, branch: str) -> LocalSandbox:
        """Create a git worktree on a fresh ``branch`` off HEAD and return a sandbox for it. This
        gives each parallel agent a fully independent working directory + branch that share one
        ``.git`` — the clean way to run several agents at once and merge their work afterwards."""
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", branch).strip("-") or "wt"
        wt = self.dir.parent / ".foundry-worktrees" / f"{self.dir.name}-{slug}"
        if wt.exists():
            await self.remove_worktree_path(wt)
        wt.parent.mkdir(parents=True, exist_ok=True)
        await self.git("worktree", "add", "-q", "-b", branch, str(wt), "HEAD")
        return LocalSandbox(wt)

    async def merge_branch(self, branch: str) -> tuple[bool, str]:
        """Merge ``branch`` into the current branch. Returns (ok, output); on a conflict it aborts
        cleanly and returns ok=False so the caller can decide (never leaves a half-merged tree)."""
        res = await self.git("merge", "--no-edit", branch)
        if res.code == 0:
            return True, (res.stdout + res.stderr).strip()
        await self.git("merge", "--abort")
        return False, (res.stdout + res.stderr).strip()[:400]

    async def remove_worktree_path(self, path: Path) -> None:
        """Remove a worktree (best-effort): detach it from git, then delete the directory."""
        try:
            await self.git("worktree", "remove", "--force", str(path))
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
        shutil.rmtree(path, ignore_errors=True)  # no-op if already gone
        await self.git("worktree", "prune")

    async def destroy(self) -> None:
        if self.dir.exists():
            shutil.rmtree(self.dir, ignore_errors=True)
