"""Claude Code CLI provider — text completion, the streamed build, honest auth failure, and the
FailoverProvider unwrap that routes a build through the CLI. The subprocess seams (`_capture` /
`_stream_lines`) are overridden so nothing real is spawned; we assert on the parsing + mapping.
"""

from __future__ import annotations

import json

import pytest
from app.agent import _as_cli_provider, run_cli_agent
from app.providers import ClaudeCliProvider, FailoverProvider, build_provider
from app.providers.base import ProviderError


class _FakeSandbox:
    def __init__(self, path: str = "/tmp/foundry-test-wt") -> None:
        self.dir = path


def _complete_json(**over) -> str:
    base = {
        "type": "result", "subtype": "success", "is_error": False, "result": "the answer",
        "usage": {"input_tokens": 100, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 0,
                  "output_tokens": 200},
        "total_cost_usd": 0.05, "modelUsage": {"claude-opus-4-8": {"inputTokens": 150}},
    }
    base.update(over)
    return json.dumps(base)


# ---- complete() (reasoning phases) --------------------------------------------------------------

async def test_complete_parses_text_tokens_and_cost():
    class P(ClaudeCliProvider):
        captured_argv: list[str] = []

        async def _capture(self, argv, cwd, timeout_s):  # noqa: ARG002
            P.captured_argv = argv
            return _complete_json()

    res = await P(model="opus", effort="high").complete(system="sys", prompt="q")
    assert res.text == "the answer"
    assert res.tokens_in == 150  # input + cache_read + cache_creation
    assert res.tokens_out == 200
    assert res.cost_cents == 5  # 0.05 USD
    assert res.model == "claude-opus-4-8"  # from modelUsage
    # restricted + model/effort + writes disabled; prompt present
    assert "--restricted" in P.captured_argv
    assert P.captured_argv[P.captured_argv.index("--model") + 1] == "opus"
    assert P.captured_argv[P.captured_argv.index("--effort") + 1] == "high"
    assert "--disallowedTools" in P.captured_argv


async def test_complete_auto_omits_model_and_effort():
    class P(ClaudeCliProvider):
        captured_argv: list[str] = []

        async def _capture(self, argv, cwd, timeout_s):  # noqa: ARG002
            P.captured_argv = argv
            return _complete_json()

    await P(model="auto", effort="auto").complete(system="", prompt="q")
    assert "--model" not in P.captured_argv
    assert "--effort" not in P.captured_argv


async def test_complete_auth_error_raises_relogin_hint():
    class P(ClaudeCliProvider):
        async def _capture(self, argv, cwd, timeout_s):  # noqa: ARG002
            return _complete_json(is_error=True,
                                  result="Failed to authenticate: OAuth session expired")

    with pytest.raises(ProviderError, match="claude auth login"):
        await P().complete(system="", prompt="q")


async def test_complete_generic_error_raises():
    class P(ClaudeCliProvider):
        async def _capture(self, argv, cwd, timeout_s):  # noqa: ARG002
            return _complete_json(is_error=True, result="model overloaded")

    with pytest.raises(ProviderError, match="overloaded"):
        await P().complete(system="", prompt="q")


# ---- iter_build_events / run_cli_agent (build phase) --------------------------------------------

_STREAM = [
    {"type": "system", "subtype": "init"},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Creating the app"},
        {"type": "tool_use", "name": "Write", "input": {"file_path": "src/App.tsx"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Edit", "input": {"file_path": "src/App.tsx"}},
        {"type": "tool_use", "name": "Bash", "input": {"command": "npm test"}}]}},
    {"type": "result", "subtype": "success", "is_error": False, "result": "Built the app",
     "usage": {"input_tokens": 300, "cache_read_input_tokens": 0, "output_tokens": 800},
     "total_cost_usd": 0.0},
]


def _cli_streaming(lines: list[dict]) -> type[ClaudeCliProvider]:
    class P(ClaudeCliProvider):
        build_argv: list[str] = []

        async def _stream_lines(self, argv, cwd, timeout_s):  # noqa: ARG002
            P.build_argv = argv
            for obj in lines:
                yield json.dumps(obj) + "\n"

    return P


async def test_run_cli_agent_maps_events_counts_files_and_meters():
    events: list[tuple] = []

    async def on_event(name, inp, out):
        events.append((name, inp, out))

    turns: list[int] = []

    async def on_turn(i):
        turns.append(i)

    cli = _cli_streaming(_STREAM)(model="sonnet")
    res = await run_cli_agent(cli, _FakeSandbox(), "build me an app",
                              system="sys", on_event=on_event, on_turn=on_turn)
    # Two distinct write ops on the SAME file → 1 file written (deduped by path).
    assert res.files_written == 1
    assert res.tokens_in == 300 and res.tokens_out == 800
    assert res.steps == 2  # two assistant turns
    assert turns == [1, 2]
    names = [e[0] for e in events]
    assert "fs_write" in names and "cmd_run" in names
    cmd = next(e for e in events if e[0] == "cmd_run")
    assert cmd[1] == {"argv": ["npm test"]}


async def test_run_cli_agent_raises_on_error_result():
    stream = [{"type": "result", "is_error": True,
               "result": "OAuth session expired and could not be refreshed"}]
    cli = _cli_streaming(stream)()
    with pytest.raises(ProviderError, match="claude auth login"):
        await run_cli_agent(cli, _FakeSandbox(), "build", system="s")


async def test_build_stream_without_result_is_treated_as_failure():
    # A crash mid-stream (no result line) must NOT look like a successful empty build.
    stream = [{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Write", "input": {"file_path": "a.ts"}}]}}]
    cli = _cli_streaming(stream)()
    with pytest.raises(ProviderError, match="without a result"):
        await run_cli_agent(cli, _FakeSandbox(), "build", system="s")


def test_build_argv_safe_vs_full_autonomy():
    safe = ClaudeCliProvider(autonomy="safe")._build_argv("t", "sys", "/wt")
    assert "acceptEdits" in safe and "--allowedTools" in safe
    assert "bypassPermissions" not in safe
    full = ClaudeCliProvider(autonomy="full")._build_argv("t", "sys", "/wt")
    assert "bypassPermissions" in full
    assert "stream-json" in full and "--verbose" in full


# ---- provider routing + failover unwrap ---------------------------------------------------------

def test_build_provider_routes_claude_cli():
    p = build_provider("claude_cli", "opus", config={"effort": "max", "autonomy": "full"})
    assert isinstance(p, ClaudeCliProvider)
    assert p.model == "opus" and p._effort == "max" and p._autonomy == "full"


def test_as_cli_provider_unwraps_failover_first_choice():
    cli = ClaudeCliProvider()
    assert _as_cli_provider(cli) is cli
    assert _as_cli_provider(FailoverProvider([cli])) is cli
    # Only when the CLI is the FIRST choice (that's the selected model).
    other = build_provider("ollama", "qwen2.5:7b")
    assert _as_cli_provider(FailoverProvider([other, cli])) is None
    assert _as_cli_provider(other) is None
