"""Live preview: run a BUILT app so the user can open and test it in a browser.

A shipped/built app can be launched on a free localhost port and its URL handed back to the UI
("Run app" on the mission). Reuses the QA harness's detection + launch helpers, but keeps the
server PROCESS ALIVE (the harness serve() is a context manager that tears down on exit) and tracks
it per mission so it can be reported and stopped. Never runs anything but the app's own start
command / a static server; binds to 127.0.0.1 only.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from dataclasses import dataclass
from pathlib import Path

from .qa_harness.detect import WebKind, probe
from .qa_harness.serve import (
    _free_port,
    _node_start_argv,
    _npm_install_if_needed,
    _wait_ready,
)


@dataclass(slots=True)
class Preview:
    mission_id: str
    port: int
    url: str
    proc: asyncio.subprocess.Process
    kind: str

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None


class PreviewError(RuntimeError):
    """Could not start a preview (not a web app, no build on disk, server didn't come up)."""


class PreviewManager:
    """Per-process registry of running app previews, keyed by mission id."""

    def __init__(self, *, ready_timeout_s: float = 25.0, npm_timeout_s: float = 180.0) -> None:
        self._previews: dict[str, Preview] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._ready_timeout_s = ready_timeout_s
        self._npm_timeout_s = npm_timeout_s

    def status(self, mission_id: str) -> dict | None:
        pv = self._previews.get(mission_id)
        if pv is None:
            return None
        if not pv.alive:  # died on its own — forget it
            self._previews.pop(mission_id, None)
            return None
        return {"running": True, "url": pv.url, "port": pv.port, "kind": pv.kind}

    async def start(self, mission_id: str, project_path: str | None) -> dict:
        """Launch (or reuse) a preview of the app at ``project_path``. Returns {url, port, kind}.

        Serialized per mission: two concurrent clicks/requests for the same mission would otherwise
        each pass the reuse check and spawn a second server, leaking a process and a port. The lock
        setdefault is safe without its own guard — there is no await between get and set."""
        lock = self._locks.setdefault(mission_id, asyncio.Lock())
        async with lock:
            return await self._start_locked(mission_id, project_path)

    async def _start_locked(self, mission_id: str, project_path: str | None) -> dict:
        current = self.status(mission_id)
        if current:
            return current
        root = Path(os.path.expanduser(project_path)) if project_path else None  # noqa: ASYNC240
        if root is None or not root.is_dir():  # noqa: ASYNC240 — a cheap local stat, like the sandbox
            raise PreviewError("no built project on disk to run yet")
        pr = probe(str(root))
        if not pr.is_web:
            raise PreviewError("this project isn't a runnable web app")

        # Prefer the app's OWN server (real API + UI). If it can't come up, fall back to a static
        # file server so at least the UI is viewable.
        if pr.kind is WebKind.NODE_SERVED:
            argv = _node_start_argv(pr.package_json or {}, pr.node_entry)
            if argv is not None and await _npm_install_if_needed(root, pr.package_json or {}, self._npm_timeout_s):
                port = _free_port()
                env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1"}
                proc: asyncio.subprocess.Process | None = None
                with contextlib.suppress(OSError):
                    proc = await asyncio.create_subprocess_exec(
                        *argv, cwd=str(root), env=env, start_new_session=True,
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    )
                if proc is not None:
                    base = f"http://127.0.0.1:{port}"
                    if await _wait_ready(base + "/", self._ready_timeout_s):
                        self._previews[mission_id] = Preview(mission_id, port, base, proc, "node")
                        return {"running": True, "url": base, "port": port, "kind": "node"}
                    await _kill(proc)  # didn't come up → fall through to a static serve

        port = _free_port()
        env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1"}
        serve_dir = root
        if pr.index_html and "/" in pr.index_html:
            serve_dir = root / pr.index_html.rsplit("/", 1)[0]
        proc = await asyncio.create_subprocess_exec(
            "python3", "-m", "http.server", str(port), "--bind", "127.0.0.1",
            cwd=str(serve_dir), env=env, start_new_session=True,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        if not await _wait_ready(base + "/", self._ready_timeout_s):
            await _kill(proc)
            raise PreviewError("the app did not start (it may have crashed on boot)")
        self._previews[mission_id] = Preview(mission_id, port, base, proc, "static")
        return {"running": True, "url": base, "port": port, "kind": "static"}

    async def stop(self, mission_id: str) -> bool:
        pv = self._previews.pop(mission_id, None)
        if pv is None:
            return False
        await _kill(pv.proc)
        return True

    async def stop_all(self) -> None:
        for mid in list(self._previews):
            await self.stop(mid)


async def _kill(proc: asyncio.subprocess.Process) -> None:
    """Terminate a preview process and its children (npm may spawn node), releasing the port."""
    if proc.returncode is not None:
        return

    def _sig(sig: int) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(proc.pid), sig)

    _sig(signal.SIGTERM)
    with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
        await asyncio.wait_for(proc.wait(), timeout=5)
    if proc.returncode is None:
        _sig(signal.SIGKILL)
