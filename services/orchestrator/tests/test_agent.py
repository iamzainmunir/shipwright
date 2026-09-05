"""Phase 4 — the tool-using agent authors the change itself (real sandbox, mock tool loop)."""

from __future__ import annotations

import re

from app.agent import run_agent
from app.devloop import TEST_SCRIPT, VULN_HANDLER
from app.sandbox import LocalSandbox
from app.tools import tool_specs

from tests.support.scripted_provider import ScriptedProvider


def test_tool_names_are_provider_valid() -> None:
    # Anthropic (incl. any Anthropic model via OpenRouter) rejects tool names that aren't
    # ^[a-zA-Z0-9_-]{1,64}$ — dotted names like "fs.write" caused a hard 400 that killed builds.
    # Keep every tool name within that pattern so ALL providers accept them.
    pat = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
    names = [t.name for t in tool_specs()]
    assert names, "expected at least one tool"
    bad = [n for n in names if not pat.match(n)]
    assert not bad, f"tool names must match ^[a-zA-Z0-9_-]{{1,64}}$ (Anthropic); offenders: {bad}"


async def test_agent_authors_fix_and_greens_the_tests(tmp_path):
    sb = await LocalSandbox.create(str(tmp_path), "agent-test")
    await sb.init_repo({
        ".gitignore": "__pycache__/\n*.pyc\n",
        "src/handler.py": VULN_HANDLER,
        "tests/run_tests.py": TEST_SCRIPT,
    })
    await sb.checkout_branch("fix/agent-test")

    result = await run_agent(ScriptedProvider(), sb, "Fix the cross-tenant read and run the tests.")
    try:
        # the agent used the tools (read → write → run tests) and got them green
        assert result.tests_passed is True
        assert any(x.startswith("fs_read") for x in result.tool_log)
        assert any(x.startswith("fs_write") for x in result.tool_log)
        assert any(x.startswith("cmd_run") for x in result.tool_log)

        # the file was actually changed by the agent, and (once committed) the diff is real
        content = await sb.read_file("src/handler.py")
        assert 'ctx["workspace"]' in content  # the tenant guard the agent wrote
        await sb.commit_all("apply agent change")
        diff = await sb.diff("main")
        assert "get_record" in diff and diff.strip()
    finally:
        await sb.destroy()
