"""The build loop must never let a model produce ZERO files by replying with prose.

Regression for M-161: gpt-oss-120b replied with a markdown architecture description and no tool
calls, so run_agent broke after 1 turn and the build shipped only scaffold (README/.gitignore),
which QA correctly bounced forever. Two defenses are tested here:
  * a NUDGE that pushes a prose-only model back to calling fs_write, and
  * a SALVAGE that turns fenced code blocks with an explicit path into real file writes.
"""

from __future__ import annotations

from app.agent import AgentResult, run_agent, salvage_file_writes
from app.providers.base import AgentTurn, LLMResult, ToolCall
from app.sandbox import LocalSandbox


class _ProseFirstProvider:
    """Ignores tool_choice and replies with PROSE first (a model that won't tool-call), then writes
    a file after being nudged, then finishes. Mirrors the real M-161 failure shape."""

    name = "fake"
    model = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.saw_tool_choice: list[str] = []

    async def complete(self, *, system: str, prompt: str, purpose: str = "", max_tokens: int = 1024) -> LLMResult:
        return LLMResult("", "fake", 0, 0, 0)

    async def complete_tools(self, *, system, messages, tools, max_tokens=2048, tool_choice="auto") -> AgentTurn:
        self.calls += 1
        self.saw_tool_choice.append(tool_choice)
        if self.calls == 1:
            # Prose only — a plan, not code; the ```ts block has NO path so salvage can't rescue it.
            prose = "Here is my plan.\n```ts\ntype Task = { id: string }\n```\nI will build it."
            return AgentTurn(prose, [], 10, 200, 0)
        if self.calls == 2:
            write = ToolCall("w1", "fs_write",
                             {"path": "src/App.tsx", "content": "export default function App(){return null}\n"})
            return AgentTurn(None, [write], 10, 30, 0)
        return AgentTurn("Done — the app is built.", [], 5, 5, 0)


class _ProseWithPathProvider:
    """Never emits native tool calls — writes the file as a fenced block WITH a path marker, which
    salvage must convert into an fs_write. Then finishes."""

    name = "fake"
    model = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *, system: str, prompt: str, purpose: str = "", max_tokens: int = 1024) -> LLMResult:
        return LLMResult("", "fake", 0, 0, 0)

    async def complete_tools(self, *, system, messages, tools, max_tokens=2048, tool_choice="auto") -> AgentTurn:
        self.calls += 1
        if self.calls == 1:
            return AgentTurn(
                "I'll create the entry file:\n\n```tsx src/main.tsx\nconsole.log('hi')\n```\n",
                [], 10, 40, 0,
            )
        return AgentTurn("Done.", [], 5, 5, 0)


async def _fresh_repo(tmp_path) -> LocalSandbox:
    sb = await LocalSandbox.create(str(tmp_path), "recovery")
    await sb.init_repo({".gitignore": "node_modules/\n"})
    return sb


async def test_prose_only_model_is_nudged_until_it_writes_files(tmp_path):
    sb = await _fresh_repo(tmp_path)
    prov = _ProseFirstProvider()
    try:
        result: AgentResult = await run_agent(prov, sb, "Build a small app.", max_steps=8)
        # The prose turn did NOT end the build — it was nudged and ultimately wrote a real file.
        assert result.files_written == 1, "the nudge must recover a prose-only model"
        assert any(x.startswith("fs_write") for x in result.tool_log)
        assert "export default function App" in await sb.read_file("src/App.tsx")
        # While nothing was written yet, the loop asked the provider to be forced onto a tool.
        assert prov.saw_tool_choice[0] == "required"
    finally:
        await sb.destroy()


async def test_fenced_code_with_a_path_is_salvaged_into_a_file(tmp_path):
    sb = await _fresh_repo(tmp_path)
    try:
        result = await run_agent(_ProseWithPathProvider(), sb, "Build it.", max_steps=6)
        assert result.files_written == 1, "a path-marked code fence must be salvaged into a write"
        assert "console.log('hi')" in await sb.read_file("src/main.tsx")
    finally:
        await sb.destroy()


def test_salvage_recognises_path_markers_and_ignores_pathless_prose():
    # (1) fence info line names the path
    calls = salvage_file_writes("```tsx src/App.tsx\nconst a = 1\n```")
    assert [c.input["path"] for c in calls] == ["src/App.tsx"]
    # (2) a heading/inline-code path just above the fence
    calls = salvage_file_writes("**index.html**\n```html\n<h1>hi</h1>\n```")
    assert [c.input["path"] for c in calls] == ["index.html"]
    # (3) a leading `// file: path` comment inside the block
    calls = salvage_file_writes("```\n// file: src/util.ts\nexport const x = 1\n```")
    assert [c.input["path"] for c in calls] == ["src/util.ts"]
    # (4) pathless fenced snippets and plain prose are NOT turned into files
    assert salvage_file_writes("```ts\ntype T = { id: string }\n```") == []
    assert salvage_file_writes("Just a paragraph describing the plan, no code at all.") == []


def test_salvage_dedupes_by_path_keeping_the_last_version():
    text = "```a.ts\nconst v = 1\n```\nlater, revised:\n```a.ts\nconst v = 2\n```"
    calls = salvage_file_writes(text)
    assert len(calls) == 1 and calls[0].input["content"].strip().endswith("v = 2")
