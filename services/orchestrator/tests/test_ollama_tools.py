"""Ollama tool-call parsing.

Local models (qwen2.5-coder, llama-family, etc.) frequently emit a tool call as JSON TEXT in the
message content instead of Ollama's native ``tool_calls`` array. If we only read the native array the
agent loop sees "no tool calls", treats the JSON as final text, and writes NO files — so the build
produces nothing. ``_tool_calls_from_text`` recovers those calls; these tests lock that in.
"""

from __future__ import annotations

from app.providers.ollama import _tool_calls_from_text

_TOOLS = {"fs_write", "fs_read", "cmd_run"}


def test_parses_bare_json_tool_call() -> None:
    # The exact shape qwen2.5-coder:7b emits for a write.
    content = (
        '{\n  "name": "fs_write",\n  "arguments": {\n'
        '    "path": "src/App.jsx",\n'
        '    "content": "import React from \'react\';\\nfunction App(){return <div>Hi</div>;}"\n'
        "  }\n}"
    )
    calls = _tool_calls_from_text(content, _TOOLS)
    assert len(calls) == 1
    assert calls[0].name == "fs_write"
    assert calls[0].input["path"] == "src/App.jsx"
    assert "import React" in calls[0].input["content"]


def test_parses_fenced_and_tagged_tool_calls() -> None:
    fenced = 'Sure!\n```json\n{"name":"fs_write","arguments":{"path":"a.js","content":"x"}}\n```'
    assert _tool_calls_from_text(fenced, _TOOLS)[0].input["path"] == "a.js"
    tagged = '<tool_call>{"name":"fs_write","arguments":{"path":"b.js","content":"y"}}</tool_call>'
    assert _tool_calls_from_text(tagged, _TOOLS)[0].input["path"] == "b.js"


def test_ignores_prose_and_unknown_tools() -> None:
    assert _tool_calls_from_text("Here is my analysis — no tools used.", _TOOLS) == []
    assert _tool_calls_from_text('{"name":"not_a_tool","arguments":{}}', _TOOLS) == []
    # arguments given as a JSON string are decoded.
    calls = _tool_calls_from_text('{"name":"cmd_run","arguments":"{\\"command\\":[\\"ls\\"]}"}', _TOOLS)
    assert calls and calls[0].input.get("command") == ["ls"]


def test_dedupes_repeated_identical_calls() -> None:
    # The same object appearing twice (e.g. echoed inside <tool_call> AND in the body) → one call.
    obj = '{"name":"fs_write","arguments":{"path":"c.js","content":"z"}}'
    assert len(_tool_calls_from_text(f"<tool_call>{obj}</tool_call>\n{obj}", _TOOLS)) == 1
