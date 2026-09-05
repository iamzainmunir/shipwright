"""Serve the built app so the QA harness can exercise it (v2 Phase 2 — plan 06 §1b, §4).

An ``async with serve(probe) as app:`` context that guarantees the server process is killed and
its port released on exit, even on error. Ordered by preference; each rung is non-fatal — a serve
failure yields ``ServedApp(base_url=None, quality=…)`` so the harness degrades to static/text checks
rather than failing the run.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import signal
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx

from .detect import ProbeResult, WebKind


@dataclass(slots=True)
class ServedApp:
    base_url: str | None            # None ⇒ could not serve (harness falls to file/text checks)
    quality: str                    # "real" | "static" | "degraded_static" | "unserved"
    detail: str = ""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_ready(url: str, timeout_s: float) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout_s
    async with httpx.AsyncClient(timeout=2.0) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                r = await client.get(url)
                if r.status_code < 500:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.15)
    return False


@contextlib.asynccontextmanager
async def serve(
    probe: ProbeResult, *, ready_timeout_s: float = 15.0, allow_npm: bool = True,
    npm_timeout_s: float = 120.0,
) -> AsyncIterator[ServedApp]:
    """Serve ``probe``'s project so QA can exercise the REAL app. NODE_SERVED → run the app's own
    server (npm start / node entry) so the actual API + UI work — not just static files (a static
    serve makes every backend/full-stack app look broken, the M-172 degraded_static bug). Vite/React
    → production build then static-serve dist. Static HTML → python http.server. Non-web → unserved.
    Every rung is non-fatal: a serve failure degrades to the next rather than failing the run."""
    # All fallible spawning happens in _prepare(); serve() only yields + tears down. Keeping the
    # yield OUT of any except matters: with @asynccontextmanager, an exception raised by the harness
    # inside `async with serve(...)` is thrown back in at the yield — if that yield sat inside a
    # broad `except`, the consumer's real error would be swallowed and a second value re-yielded
    # ("generator didn't stop"). So catch only around setup, never around the yield.
    served, proc, group = await _prepare(
        probe, ready_timeout_s=ready_timeout_s, allow_npm=allow_npm, npm_timeout_s=npm_timeout_s,
    )
    try:
        yield served
    finally:
        await _stop(proc, group)


async def _prepare(
    probe: ProbeResult, *, ready_timeout_s: float, allow_npm: bool, npm_timeout_s: float,
) -> tuple[ServedApp, asyncio.subprocess.Process | None, bool]:
    """Spawn the best available server for ``probe`` and return (ServedApp, proc, group). Own the
    entire fallible surface here so serve()'s yield stays outside any except. ``group`` marks a
    process started in its own session (node/npm) so _stop can kill the whole tree."""
    proc: asyncio.subprocess.Process | None = None
    group = False  # node servers run in their own process group so we can kill npm's child too
    try:
        if probe.kind is WebKind.NON_WEB:
            return ServedApp(None, "unserved", "non-web project"), None, False

        project = Path(probe.project_path)

        # NODE_SERVED: boot the app's OWN server so the real routes respond. This is what lets QA
        # verify a backend/full-stack app at runtime instead of grading a dead static tree.
        if probe.kind is WebKind.NODE_SERVED and allow_npm:
            argv = _node_start_argv(probe.package_json or {}, probe.node_entry)
            if argv is not None and await _npm_install_if_needed(project, probe.package_json or {}, npm_timeout_s):
                port = _free_port()
                # The app must honour PORT (most Node servers do); we hand it a free one so QA never
                # collides with the dev server or a hardcoded 3000.
                env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1"}
                with contextlib.suppress(OSError):
                    proc = await asyncio.create_subprocess_exec(
                        *argv, cwd=str(project), env=env, start_new_session=True,
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    )
                    group = True
                    base = f"http://127.0.0.1:{port}"
                    if await _wait_ready(base + "/", ready_timeout_s):
                        return ServedApp(base, "real", f"ran {' '.join(argv)}"), proc, group
                    await _stop(proc, group)  # didn't come up → fall through to a static serve
                    proc, group = None, False

        # Vite/React: a production bundle is the real deliverable; static-serve dist/.
        serve_dir = project
        quality = "static" if probe.kind is WebKind.STATIC_HTML else "degraded_static"
        if probe.kind is WebKind.VITE_REACT and allow_npm:
            built, _why = await _try_vite_build(project, npm_timeout_s)
            if built is not None:
                serve_dir, quality = built, "real"

        # index.html may live in public/ or dist/ or src/ — serve that dir so "/" resolves.
        if probe.index_html and "/" in probe.index_html:
            serve_dir = project / probe.index_html.rsplit("/", 1)[0]

        port = _free_port()
        proc = await asyncio.create_subprocess_exec(
            "python3", "-m", "http.server", str(port), "--bind", "127.0.0.1",
            cwd=str(serve_dir),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        if await _wait_ready(base + "/", ready_timeout_s):
            return ServedApp(base, quality), proc, group
        await _stop(proc, group)
        return ServedApp(None, "unserved", "server did not become ready"), None, False
    except Exception as exc:  # noqa: BLE001 — serving must never fail the run
        await _stop(proc, group)  # tear down any half-spawned server before degrading
        return ServedApp(None, "unserved", f"serve error: {exc}"), None, False


def _node_start_argv(pkg: dict, node_entry: str | None = None) -> list[str] | None:
    """How to start a Node app's server. Prefer running ``node <entry>`` DIRECTLY (a single process
    we can cleanly kill) parsed from the ``start`` script; else ``npm start`` / ``npm run dev``; else
    the detected server entry (``node server.js`` — works even with NO package.json); else
    ``node <main>``. None ⇒ can't tell, caller falls back to a static serve."""
    scripts = pkg.get("scripts") or {}
    start = str(scripts.get("start") or "").strip()
    if start:
        with contextlib.suppress(ValueError):
            toks = shlex.split(start)
            if toks and toks[0] == "node":
                return toks  # e.g. "node src/backend/server.js" → run node directly
        return ["npm", "start"]
    if scripts.get("dev"):
        return ["npm", "run", "dev"]
    if node_entry:
        return ["node", node_entry]  # a bare server.js with no package.json is still runnable
    main = pkg.get("main")
    return ["node", str(main)] if main else None


async def _npm_install_if_needed(project: Path, pkg: dict, timeout_s: float) -> bool:
    """Install deps only when there ARE deps and node_modules is missing (a dependency-free app like a
    built-in-http server needs nothing — don't waste the budget). Returns False only if a needed
    install fails, so the caller degrades instead of launching a server that will crash on import."""
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    if not deps or (project / "node_modules").is_dir():
        return True
    try:
        code = await _run(["npm", "install", "--prefer-offline", "--no-audit", "--no-fund"],
                          project, timeout_s)
        return code == 0
    except (TimeoutError, FileNotFoundError, OSError):
        return False


async def _stop(proc: asyncio.subprocess.Process | None, group: bool) -> None:
    """Terminate a served-app process (and its children, for an npm-spawned server) and release the
    port. Never raises."""
    if proc is None or proc.returncode is not None:
        return

    def _signal(sig: int) -> None:
        if group:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(proc.pid), sig)
        else:
            with contextlib.suppress(ProcessLookupError):
                proc.send_signal(sig)

    _signal(signal.SIGTERM)
    with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
        await asyncio.wait_for(proc.wait(), timeout=5)
    if proc.returncode is None:
        _signal(signal.SIGKILL)


async def _try_vite_build(project: Path, timeout_s: float) -> tuple[Path | None, str]:
    """Best-effort real bundle: reuse node_modules or install --prefer-offline, then `npm run build`.
    Returns (dist_dir, "") on success, else (None, reason). Never raises."""
    node_modules = project / "node_modules"
    try:
        if not node_modules.is_dir():
            code = await _run(["npm", "install", "--prefer-offline", "--no-audit", "--no-fund"],
                              project, timeout_s)
            if code != 0:
                return None, "npm install failed/unavailable"
        code = await _run(["npm", "run", "build"], project, min(timeout_s, 180))
        if code != 0:
            return None, "npm run build failed"
        for out in ("dist", "build"):
            if (project / out / "index.html").is_file():
                return project / out, ""
        return None, "no dist/index.html after build"
    except (TimeoutError, FileNotFoundError, OSError) as exc:
        return None, f"build unavailable: {exc}"


async def _run(argv: list[str], cwd: Path, timeout_s: float) -> int:
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(cwd),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout_s)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise
    return proc.returncode or 0
