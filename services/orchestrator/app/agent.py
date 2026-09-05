"""Tool-using agent loop (Canon §4) — the Backend agent authors the change itself.

Runs a perceive→act loop over the provider's tool-use API: the model reads/writes files, runs
the tests, and stops when they pass. The same loop drives a real Anthropic model (when a key is
set) or the deterministic offline provider — the diff is produced by the agent's tool calls, not
hardcoded. Each tool call can be streamed to the mission console via ``on_event``.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from foundry_core import tracing

from .prompts import AGENT_SYSTEM as SYSTEM
from .prompts import MAX_NUDGES, NUDGE_NO_FILES
from .providers.base import LLMProvider, ProviderError, ToolCall
from .providers.claude_cli import ClaudeCliProvider
from .sandbox import LocalSandbox
from .tools import execute_tool, tool_specs

OnEvent = Callable[[str, dict[str, Any], str], Awaitable[None]]
OnTurn = Callable[[int], Awaitable[None]]  # fired at the start of each agent turn (step index)


@dataclass(slots=True)
class AgentResult:
    steps: int
    tests_passed: bool
    tokens_in: int
    tokens_out: int
    cost_cents: int
    tool_log: list[str] = field(default_factory=list)
    final_text: str = ""
    files_written: int = 0  # count of successful fs_write calls — 0 ⇒ the build produced nothing


_CODE_BLOCK_RE = re.compile(r"```(?P<info>[^\n]*)\n(?P<body>.*?)(?:\n)?```", re.DOTALL)
# A path with a real file extension (src/App.tsx, index.html, package.json). Rejects prose.
_PATH_RE = re.compile(r"[\w.\-/]+\.[A-Za-z0-9]{1,8}")
# A "// file: path" / "# path: path" comment on the first line inside a block.
_PATH_COMMENT_RE = re.compile(
    r"^\s*(?://|#|<!--|/\*|--)\s*(?:file|path|filename)\s*[:=]\s*(?P<p>[\w.\-/]+\.[A-Za-z0-9]{1,8})",
    re.IGNORECASE,
)


def _looks_like_path(token: str) -> str | None:
    """Return a clean repo-relative path if ``token`` names a file, else None (conservative)."""
    token = token.strip().strip("`*#:\"' ")
    m = _PATH_RE.fullmatch(token)
    return token if m and " " not in token else None


def salvage_file_writes(text: str, max_files: int = 24) -> list[ToolCall]:
    """Rescue a model that wrote files as PROSE (fenced code blocks) instead of calling fs_write.

    Conservative: a block becomes an fs_write ONLY when we can resolve an explicit path for it —
    from the fence info line (```tsx src/App.tsx), a heading/inline-code just above the fence, or a
    leading ``// file: path`` comment inside. Blocks with no resolvable path are ignored, so ordinary
    prose or pathless snippets never become spurious files."""
    calls: list[ToolCall] = []
    for m in _CODE_BLOCK_RE.finditer(text or ""):
        body = m.group("body")
        if not body.strip():
            continue
        path: str | None = None
        # 1) fence info line, e.g. ```tsx src/App.tsx  or  ```src/App.tsx  or  ```ts title="a.ts"
        for tok in re.split(r"[\s\"'=]+", m.group("info") or ""):
            path = path or _looks_like_path(tok)
        # 2) the last non-empty line just before the fence (a heading / **bold** / `inline` path)
        if not path:
            before = [ln for ln in (text[: m.start()]).splitlines() if ln.strip()]
            if before:
                for tok in re.findall(r"[\w.\-/]+\.[A-Za-z0-9]{1,8}", before[-1]):
                    path = path or _looks_like_path(tok)
        # 3) a "// file: path" comment on the first body line
        if not path:
            first = body.splitlines()[0] if body.splitlines() else ""
            cm = _PATH_COMMENT_RE.match(first)
            if cm:
                path = cm.group("p")
        if not path:
            continue
        calls.append(ToolCall(id=f"salv_{len(calls)}", name="fs_write",
                              input={"path": path, "content": body if body.endswith("\n") else body + "\n"}))
        if len(calls) >= max_files:
            break
    # De-dupe by path (keep the last/most-complete version the model produced).
    by_path: dict[str, ToolCall] = {}
    for c in calls:
        by_path[str(c.input.get("path"))] = c
    return list(by_path.values())


def _short_input(inp: dict[str, Any]) -> str:
    d = dict(inp)
    if "content" in d:
        d["content"] = f"<{len(str(d['content']))} chars>"
    return ", ".join(f"{k}={v}" for k, v in d.items())[:80]


def _as_cli_provider(provider: LLMProvider) -> ClaudeCliProvider | None:
    """The Claude CLI provider builds as its OWN agent, not via the tool loop. Return it when the
    build should run through the CLI — directly, or when it's the FIRST choice of a FailoverProvider
    chain (the parallel fan-out wraps each agent's chain in one). ``None`` ⇒ use the normal loop."""
    if isinstance(provider, ClaudeCliProvider):
        return provider
    chain = getattr(provider, "_providers", None)  # FailoverProvider
    if chain and isinstance(chain[0], ClaudeCliProvider):
        return chain[0]
    return None


async def run_cli_agent(
    cli: ClaudeCliProvider, sandbox: LocalSandbox, task: str, *,
    system: str | None = None, on_event: OnEvent | None = None,
    on_turn: OnTurn | None = None, owned_paths: list[str] | None = None,
) -> AgentResult:
    """Build via the ``claude`` binary running as a coding agent in ``sandbox`` (its git worktree —
    the ownership boundary). Streams the CLI's events onto the same mission console the tool loop
    uses, and returns the same :class:`AgentResult` so callers don't care which built it."""
    prompt = task
    if owned_paths:
        # Advisory (the worktree is the hard boundary): keep the agent focused on its slice.
        prompt = f"{task}\n\nFocus your changes on these files/areas: {', '.join(owned_paths)}."
    steps = ti = to = cc = 0
    written: set[str] = set()
    tool_log: list[str] = []
    tests_passed = False
    final = ""
    async for ev in cli.iter_build_events(workdir=str(sandbox.dir), task=prompt, system=system or SYSTEM):
        kind = ev.get("kind")
        if kind == "turn":
            steps += 1
            if on_turn is not None:
                await on_turn(steps)
        elif kind == "tool":
            name, inp = ev.get("name", ""), ev.get("input") or {}
            if name == "fs_write" and inp.get("path"):
                written.add(str(inp["path"]))
            tool_log.append(f"{name}({_short_input(inp)})")
            if on_event is not None:
                await on_event(name, inp, ev.get("output", ""))
        elif kind == "result":
            ti, to, cc = ev.get("tokens_in", 0), ev.get("tokens_out", 0), ev.get("cost_cents", 0)
            final = ev.get("text", "") or ""
            if ev.get("is_error"):
                raise ProviderError(ev.get("error") or "claude CLI build failed")
    # tests_passed stays False (the CLI doesn't report a green test run here); QA is the real gate.
    return AgentResult(steps or 1, tests_passed, ti, to, cc, tool_log, final, len(written))


async def run_agent(
    provider: LLMProvider, sandbox: LocalSandbox, task: str, *,
    max_steps: int = 8, system: str | None = None, on_event: OnEvent | None = None,
    on_turn: Callable[[int], Awaitable[None]] | None = None,
    owned_paths: list[str] | None = None,
) -> AgentResult:
    """``owned_paths`` (v2): when set, fs_write is jailed to the agent's slice of the ownership
    map (parallel workers). None = unrestricted (solo build / integrator)."""
    system_prompt = system or SYSTEM
    # The Claude CLI is its own coding agent — delegate the whole build to it (it writes files
    # directly in ``sandbox``) instead of driving the turn-by-turn tool loop below.
    cli = _as_cli_provider(provider)
    if cli is not None:
        return await run_cli_agent(
            cli, sandbox, task, system=system_prompt,
            on_event=on_event, on_turn=on_turn, owned_paths=owned_paths,
        )
    tools = tool_specs()
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    tests_passed = False
    ti = to = cc = steps = 0
    tool_log: list[str] = []
    final = ""
    files_written = 0  # successful fs_write count — the real measure of build progress
    nudges = 0         # prose replies we've corrected so far

    for _ in range(max_steps):
        steps += 1
        # Announce the turn BEFORE the (slow) model call, so the console shows continuous progress.
        if on_turn is not None:
            await on_turn(steps)
        # Force a tool call until real work has landed: open models on some providers reply with
        # PROSE on the first turn and the build produces 0 files. Once files exist, let the model
        # choose (it may legitimately finish). Providers that can't honour "required" fall back.
        tool_choice = "required" if files_written == 0 else "auto"
        with tracing.span(
            "agent.turn",
            {tracing.ATTR_TURN_INDEX: steps, tracing.ATTR_AGENT_ROLE: "backend"},
        ) as turn_span:
            turn = await provider.complete_tools(
                system=system_prompt, messages=messages, tools=tools, tool_choice=tool_choice
            )
            ti += turn.tokens_in
            to += turn.tokens_out
            cc += turn.cost_cents

            calls = list(turn.tool_calls)
            salvaged = False
            # The model wrote files as prose (fenced code) instead of calling fs_write → rescue them.
            if not calls and turn.text:
                calls = salvage_file_writes(turn.text)
                salvaged = bool(calls)
            tracing.set_attributes(
                turn_span, {tracing.ATTR_STOP_REASON: "end_turn" if not calls else "tool_use"}
            )

            if not calls:
                # No tool calls and nothing salvageable. If the build produced NOTHING yet, the model
                # is stalling in prose — nudge it back to writing files and retry (bounded). Only
                # accept "done with no tools" once real work exists.
                if files_written == 0 and nudges < MAX_NUDGES:
                    nudges += 1
                    if turn.text:
                        messages.append({"role": "assistant", "content": [{"type": "text", "text": turn.text}]})
                    messages.append({"role": "user", "content": NUDGE_NO_FILES})
                    continue
                final = turn.text or ""
                break

            assistant: list[dict[str, Any]] = []
            if turn.text and not salvaged:
                assistant.append({"type": "text", "text": turn.text})
            for tc in calls:
                assistant.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input})
            messages.append({"role": "assistant", "content": assistant})

            results: list[dict[str, Any]] = []
            for tc in calls:
                with tracing.span("tool.call", {tracing.ATTR_TOOL_NAME: tc.name}) as tool_span:
                    out, meta = await execute_tool(sandbox, tc, owned_paths=owned_paths)
                    ran_tests = tc.name == "cmd_run" and any(
                        "run_tests" in str(a) for a in (meta.get("argv") or [])
                    )
                    ok = meta.get("exit", 0) == 0 if tc.name == "cmd_run" else True
                    tracing.set_attributes(tool_span, {tracing.ATTR_TOOL_OK: ok})
                if ran_tests and ok:
                    tests_passed = True
                if tc.name == "fs_write" and not meta.get("error"):
                    files_written += 1
                tool_log.append(
                    f"{tc.name}({_short_input(tc.input)}) → {out.splitlines()[0][:80] if out else ''}"
                )
                if on_event is not None:
                    await on_event(tc.name, tc.input, out)
                results.append({"type": "tool_result", "tool_use_id": tc.id, "content": out})
            messages.append({"role": "user", "content": results})

    return AgentResult(steps, tests_passed, ti, to, cc, tool_log, final, files_written)
