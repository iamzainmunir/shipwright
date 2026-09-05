"""Claude Code CLI adapter — drives the official ``claude`` binary as a subprocess.

Why a subprocess and not the token? A Claude *subscription* seat (Pro/Max/Team/Enterprise, the
OAuth login) may only be used programmatically through Anthropic's own product — the ``claude``
binary. That is sanctioned, scripted use. Lifting the OAuth token out of the CLI's keychain and
feeding it to the API/SDK is a Consumer-ToS violation, so we never do that: we shell out to the
real binary and let *it* hold the credential.

Two surfaces:

* :meth:`complete` — the reasoning phases (spec/plan/review/QA-verdict/readme). Runs
  ``claude -p --output-format json`` in restricted mode (no shell, file writes disabled) in a
  throwaway cwd, so it purely answers. Returns real token/cost usage from the CLI's result JSON.
* :meth:`iter_build_events` — the build phase. The CLI is *itself* a coding agent, so instead of
  the turn-by-turn tool loop (:meth:`complete_tools`, which the CLI cannot serve and which therefore
  raises) the build path streams ``--output-format stream-json`` and yields NORMALISED events the
  agent loop maps onto the mission console (see ``run_cli_agent`` in ``agent.py``). Files land on
  disk in the agent's own git worktree — the worktree IS the ownership boundary.

Honest failure: a logged-out / expired seat is detected from the CLI's own error and raised as a
:class:`ProviderError` telling the user to run ``claude auth login`` — never a fabricated answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import tempfile
from collections.abc import AsyncIterator
from typing import Any

from .base import AgentTurn, LLMResult, ProviderError, ToolSpec

# Model aliases the CLI understands via --model. "auto" ⇒ omit the flag (the session default picks).
_MODEL_ALIASES = {"auto", "opus", "sonnet", "haiku", "fable"}
# Effort levels the CLI accepts via --effort. "auto" ⇒ omit the flag.
_EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}

# Build autonomy: "safe" writes files but runs no shell (can't hang on a Bash approval in headless
# mode); "full" is fully autonomous (scaffold, install, run tests) inside the isolated worktree.
_SAFE_TOOLS = "Read,Write,Edit,MultiEdit,Glob,Grep,LS,TodoWrite"
_TEXT_DISALLOWED = "Bash,Write,Edit,MultiEdit,NotebookEdit,Task,WebSearch,WebFetch"

# An error whose text matches this means the seat is signed out — actionable, not a transient fault.
_AUTH_HINT = "Sign in to Claude Code first: run `claude auth login` (your Claude subscription seat)."


def _is_auth_error(text: str) -> bool:
    t = (text or "").lower()
    return any(s in t for s in ("oauth", "authenticate", "auth failed", "logged out",
                                "not logged in", "log in", "login", "unauthorized", "401"))


def _cost_cents(total_cost_usd: float) -> int:
    """Subscription seats bill no marginal per-token cost, so this is usually 0; when the CLI does
    report a dollar figure (API-key sessions), meter it truthfully."""
    try:
        return round(float(total_cost_usd) * 100)
    except (TypeError, ValueError):
        return 0


def _usage_tokens(usage: dict[str, Any]) -> tuple[int, int]:
    """(input, output) from the CLI's usage block, counting cache reads/writes as real input."""
    def _i(k: str) -> int:
        with contextlib.suppress(TypeError, ValueError):
            return int(usage.get(k, 0) or 0)
        return 0

    tin = _i("input_tokens") + _i("cache_read_input_tokens") + _i("cache_creation_input_tokens")
    return tin, _i("output_tokens")


# ---- normalised build events (what iter_build_events yields; agent.py interprets these) ----------
# {"kind": "turn"}                                         a new assistant turn began
# {"kind": "tool", "name": <fs_write|fs_read|cmd_run>, "input": {...}, "output": str}
# {"kind": "result", "tokens_in", "tokens_out", "cost_cents", "is_error", "text", "error"}

# CLI tool → Shipwright console tool name (so the live feed matches the tool-loop's shape).
_TOOL_MAP = {
    "Write": "fs_write", "Edit": "fs_write", "MultiEdit": "fs_write", "NotebookEdit": "fs_write",
    "Read": "fs_read", "NotebookRead": "fs_read", "Glob": "fs_read", "Grep": "fs_read", "LS": "fs_read",
    "Bash": "cmd_run",
}
_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _tool_event(name: str, tool_input: dict[str, Any]) -> dict[str, Any] | None:
    """Map one CLI ``tool_use`` block to a Shipwright console event, or None to omit it from the feed."""
    mapped = _TOOL_MAP.get(name)
    if mapped is None:
        return None
    if mapped == "cmd_run":
        cmd = tool_input.get("command") or tool_input.get("cmd") or ""
        return {"kind": "tool", "name": "cmd_run", "input": {"argv": [cmd]}, "output": ""}
    path = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path") \
        or tool_input.get("pattern") or ""
    return {"kind": "tool", "name": mapped, "input": {"path": path}, "output": ""}


class ClaudeCliProvider:
    """LLM provider backed by the local ``claude`` binary (Claude Code)."""

    name = "claude_cli"

    def __init__(
        self, model: str = "auto", *, effort: str = "auto", binary: str | None = None,
        autonomy: str = "safe", timeout: float = 180.0, build_timeout: float = 1800.0,
    ) -> None:
        self.model = (model or "auto").strip().lower()
        self._effort = (effort or "auto").strip().lower()
        self._binary = binary or "claude"
        self._autonomy = (autonomy or "safe").strip().lower()
        self._timeout = timeout
        self._build_timeout = build_timeout

    # ---- argv helpers -----------------------------------------------------------------------

    def _model_args(self) -> list[str]:
        return [] if self.model in ("", "auto") else ["--model", self.model]

    def _effort_args(self) -> list[str]:
        return ["--effort", self._effort] if self._effort in _EFFORT_LEVELS else []

    # ---- low-level subprocess seams (overridden in tests) -----------------------------------

    async def _capture(self, argv: list[str], cwd: str, timeout_s: float) -> str:
        """Run ``argv`` to completion and return stdout. Raises ProviderError on spawn failure."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise ProviderError(
                f"claude CLI not found ({self._binary}). Install Claude Code, then `claude auth login`."
            ) from exc
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError as exc:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()  # reap the killed child so it isn't left as a zombie
            raise ProviderError(f"claude CLI timed out after {timeout_s:.0f}s") from exc
        if proc.returncode not in (0, None) and not out:
            err_text = err.decode(errors="replace")
            if _is_auth_error(err_text):  # a signed-out seat surfaces on stderr, not stdout
                raise ProviderError(_AUTH_HINT)
            raise ProviderError(f"claude CLI exited {proc.returncode}: {err_text[:200]}")
        return out.decode(errors="replace")

    async def _stream_lines(self, argv: list[str], cwd: str, timeout_s: float) -> AsyncIterator[str]:
        """Spawn ``argv`` and yield stdout lines as they arrive (for stream-json). Enforces an
        overall wall-clock ``timeout_s`` (there is no --max-turns in this CLI version).

        stderr is discarded (not PIPEd): we only ever drain stdout here, and an unread stderr pipe
        can fill its OS buffer and deadlock the child mid-build. The stream-json result event on
        stdout already carries ``is_error``/auth failures, so nothing diagnostic is lost."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise ProviderError(
                f"claude CLI not found ({self._binary}). Install Claude Code, then `claude auth login`."
            ) from exc
        assert proc.stdout is not None
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_s
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise ProviderError(f"claude CLI build timed out after {timeout_s:.0f}s")
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                except TimeoutError as exc:
                    raise ProviderError(f"claude CLI build timed out after {timeout_s:.0f}s") from exc
                if not line:
                    break
                yield line.decode(errors="replace")
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()

    # ---- LLMProvider protocol ---------------------------------------------------------------

    async def complete(
        self, *, system: str, prompt: str, purpose: str = "", max_tokens: int = 1024
    ) -> LLMResult:
        """A one-shot text answer (reasoning phases). Restricted + file-writes disabled + a throwaway
        cwd, so the CLI purely reasons and returns text."""
        _ = purpose, max_tokens  # the CLI manages its own output budget
        tmp = tempfile.mkdtemp(prefix="foundry-cli-")
        argv = [
            self._binary, "-p", prompt,
            "--output-format", "json",
            "--restricted",
            *self._model_args(), *self._effort_args(),
        ]
        if system:
            argv += ["--append-system-prompt", system]
        argv += ["--disallowedTools", _TEXT_DISALLOWED]  # variadic option kept last
        try:
            raw = await self._capture(argv, cwd=tmp, timeout_s=self._timeout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        data = _parse_result_json(raw)
        text = str(data.get("result") or "")
        if data.get("is_error"):
            if _is_auth_error(text):
                raise ProviderError(_AUTH_HINT)
            raise ProviderError(f"claude CLI error: {text[:200] or 'unknown'}")
        tin, tout = _usage_tokens(data.get("usage") or {})
        model = next(iter((data.get("modelUsage") or {}).keys()), None) or self.model
        return LLMResult(text=text, model=model, tokens_in=tin, tokens_out=tout,
                         cost_cents=_cost_cents(data.get("total_cost_usd", 0)))

    async def complete_tools(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec],
        max_tokens: int = 2048, tool_choice: str = "auto",
    ) -> AgentTurn:
        """Not supported: the CLI runs its own agent loop, so the build path uses
        :meth:`iter_build_events` (via ``run_cli_agent``) instead of a turn-by-turn tool loop."""
        raise ProviderError(
            "Claude CLI builds run as an autonomous agent, not via complete_tools — this is a wiring bug."
        )

    # ---- build surface ----------------------------------------------------------------------

    def _build_argv(self, task: str, system: str, workdir: str) -> list[str]:
        argv = [
            self._binary, "-p", task,
            "--output-format", "stream-json", "--verbose",
            "--add-dir", workdir,
            *self._model_args(), *self._effort_args(),
        ]
        if system:
            argv += ["--append-system-prompt", system]
        if self._autonomy == "full":
            # Fully autonomous inside the isolated worktree: scaffold, install deps, run tests.
            argv += ["--permission-mode", "bypassPermissions"]
        else:
            # Safe: auto-accept file edits, but no shell — so a headless run never hangs on a Bash
            # approval it can't answer. Restricting the toolset makes the agent build with files only.
            argv += ["--permission-mode", "acceptEdits", "--allowedTools", _SAFE_TOOLS]
        return argv

    async def iter_build_events(
        self, *, workdir: str, task: str, system: str = ""
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a build in ``workdir`` and yield normalised events (see module docstring)."""
        argv = self._build_argv(task, system, workdir)
        emitted_result = False
        async for line in self._stream_lines(argv, cwd=workdir, timeout_s=self._build_timeout):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            typ = obj.get("type")
            if typ == "assistant":
                yield {"kind": "turn"}
                for block in (obj.get("message") or {}).get("content") or []:
                    if block.get("type") == "tool_use":
                        ev = _tool_event(block.get("name", ""), block.get("input") or {})
                        if ev is not None:
                            yield ev
            elif typ == "result":
                emitted_result = True
                text = str(obj.get("result") or "")
                is_error = bool(obj.get("is_error"))
                if is_error and _is_auth_error(text):
                    raise ProviderError(_AUTH_HINT)
                tin, tout = _usage_tokens(obj.get("usage") or {})
                yield {
                    "kind": "result", "tokens_in": tin, "tokens_out": tout,
                    "cost_cents": _cost_cents(obj.get("total_cost_usd", 0)),
                    "is_error": is_error, "text": text,
                    "error": text if is_error else None,
                }
        if not emitted_result:
            # The stream ended without a result line (crash / killed) — surface it, don't pretend success.
            yield {"kind": "result", "tokens_in": 0, "tokens_out": 0, "cost_cents": 0,
                   "is_error": True, "text": "", "error": "claude CLI ended without a result"}


def _parse_result_json(raw: str) -> dict[str, Any]:
    """Parse the CLI's ``--output-format json`` result. It emits a single JSON object; be tolerant of
    leading log noise by taking the last non-empty line that parses as an object."""
    raw = (raw or "").strip()
    if not raw:
        raise ProviderError("claude CLI returned no output — is it installed and logged in?")
    with contextlib.suppress(json.JSONDecodeError):
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line:
            continue
        with contextlib.suppress(json.JSONDecodeError):
            obj = json.loads(line)
            if isinstance(obj, dict):
                return obj
    raise ProviderError(f"claude CLI returned unparseable output: {raw[:200]}")
