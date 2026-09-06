"""Multi-project coordinated change — the build fans out across selected repos, each on its own
``fix/`` branch, with per-repo build facts. Offline (a deterministic writer provider)."""
from __future__ import annotations

import json
import subprocess

from app.engine import RunEngine
from app.events import EventBus
from app.store import InMemoryStore
from foundry_core.ids import new_ulid
from foundry_core.models import Mission, Project


class _WriterProvider:
    """Build double: its ``complete_tools`` writes ONE file then finishes (so each repo gets edited)."""

    name = "writer"
    model = "writer-1"

    def __init__(self, fname: str) -> None:
        self.fname = fname

    async def complete(self, *, system, prompt, purpose="", max_tokens=1024):
        from app.providers.base import LLMResult
        return LLMResult(text="ok", model=self.model, tokens_in=1, tokens_out=1, cost_cents=0)

    async def complete_tools(self, *, system, messages, tools, max_tokens=2048, tool_choice="auto"):
        from app.providers.base import AgentTurn, ToolCall
        if f"wrote {self.fname}" in json.dumps(messages):
            return AgentTurn("built", [], 1, 1, 0)
        return AgentTurn(None, [ToolCall("w1", "fs_write", {"path": self.fname, "content": "x\n"})], 1, 1, 0)


def _git_repo(base, name: str) -> str:
    d = base / name
    d.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    (d / "README.md").write_text(f"# {name}\n")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
    return str(d)


async def test_multi_repo_build_edits_each_repo(tmp_path):
    store = InMemoryStore()
    svc = await store.create_project(Project(
        id=new_ulid(), workspace_id="w", name="svc", slug="svc",
        path=_git_repo(tmp_path, "svc"), source="registered"))
    gw = await store.create_project(Project(
        id=new_ulid(), workspace_id="w", name="gw", slug="gw",
        path=_git_repo(tmp_path, "gw"), source="registered"))

    engine = RunEngine(store, EventBus(), _WriterProvider("noop.py"))
    mission = Mission(
        id="m1", key="M-1", workspace_id="w", title="Add orders",
        requirements="Add an orders API in svc and route it in the gateway.",
        project_kind="change", project_path=svc.path, project_ids=[svc.id, gw.id],
    )

    targets = await engine._project_targets(mission)
    assert {t[0] for t in targets} == {"svc", "gw"}

    result, sb = await engine._build_multi_repo(mission, _WriterProvider("orders.py"), targets, None, None)

    repos = engine._multi_repo["m1"]
    assert len(repos) == 2
    assert all(r["branch"].startswith("fix/") for r in repos)
    assert all("orders.py" in r["files"] for r in repos)  # each repo actually got the change
    # aggregated result carries files from BOTH repos, namespaced by project
    assert any(f.startswith("svc:") for f in result.files)
    assert any(f.startswith("gw:") for f in result.files)
    assert result.files_written >= 2


async def test_single_project_mission_has_no_targets():
    store = InMemoryStore()
    engine = RunEngine(store, EventBus(), _WriterProvider("x.py"))
    mission = Mission(id="m2", key="M-2", workspace_id="w", title="Solo", project_ids=[])
    assert await engine._project_targets(mission) == []
