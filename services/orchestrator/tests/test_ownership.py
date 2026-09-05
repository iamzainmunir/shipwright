"""v2 Phase 4 — the three-layer anti-conflict system: path matching + map validation +
tool-level write enforcement + parallel-build wiring. (plan 04)"""

from __future__ import annotations

from app.ownership import find_overlaps, path_matches, remap_to_owned, validate_ownership
from app.providers.base import ToolCall
from app.sandbox import LocalSandbox
from app.tools import execute_tool

_ROLES = {"frontend", "backend", "fullstack", "devops", "qa"}


def test_remap_salvages_dropped_directory_prefix_but_blocks_out_of_lane():
    owned = ["web/index.html", "web/app.js"]
    # dropped the 'web/' prefix → remapped onto the intended owned file
    assert remap_to_owned("index.html", owned) == "web/index.html"
    assert remap_to_owned("app.js", owned) == "web/app.js"
    # already an owned path → unchanged
    assert remap_to_owned("web/app.js", owned) == "web/app.js"
    # a genuinely different file another agent owns → NOT remapped (stays blocked)
    assert remap_to_owned("api/server.py", ["web/styles.css", "README.md"]) is None
    # ambiguous filename across two owned files → not remapped (never guess)
    assert remap_to_owned("a.py", ["x/a.py", "y/a.py"]) is None


# ---- path matching -------------------------------------------------------------------

def test_path_matches_literal_and_dir_prefix() -> None:
    assert path_matches("src/api/routes.py", ["src/api/routes.py"])          # exact file
    assert path_matches("src/api/routes.py", ["src/api/"])                    # dir with slash
    assert path_matches("src/api/routes.py", ["src/api"])                     # bare dir name
    assert path_matches("src/api/routes.py", ["src/api/*"])                   # star form
    assert path_matches("./src/api/x.py", ["src/api/"])                       # ./ normalized
    assert not path_matches("src/components/Board.tsx", ["src/api/"])         # different subtree
    assert not path_matches("src/apixyz.py", ["src/api"])                     # sibling, not child


# ---- overlap detection + validation --------------------------------------------------

def test_find_overlaps_catches_dir_vs_file() -> None:
    tasks = [
        {"id": "T1", "owned_paths": ["src/api/"]},
        {"id": "T2", "owned_paths": ["src/api/routes.py"]},  # inside T1's dir → overlap
    ]
    problems = find_overlaps(tasks)
    assert problems and "T1" in problems[0] and "T2" in problems[0]


def test_validate_ownership_flags_empty_and_dupes_and_roles() -> None:
    tasks = [
        {"id": "T1", "role": "backend", "owned_paths": ["a.py"]},
        {"id": "T1", "role": "wizard", "owned_paths": []},  # dup id, bad role, empty paths
    ]
    problems = validate_ownership(tasks, _ROLES)
    joined = " | ".join(problems)
    assert "duplicate id" in joined and "unknown role 'wizard'" in joined and "non-empty" in joined


def test_validate_ownership_clean_map_has_no_problems() -> None:
    tasks = [
        {"id": "T1", "role": "backend", "owned_paths": ["src/api/", "src/store/"]},
        {"id": "T2", "role": "frontend", "owned_paths": ["src/components/", "src/styles/"]},
    ]
    assert validate_ownership(tasks, _ROLES) == []


# ---- tool-level enforcement (the load-bearing layer) ---------------------------------

async def test_fs_write_rejects_out_of_ownership(tmp_path) -> None:
    sb = await LocalSandbox.at_path(str(tmp_path / "proj"))
    await sb.init_empty_repo()
    owned = ["src/components/"]
    # In-ownership write succeeds.
    ok, meta = await execute_tool(
        sb, ToolCall("c1", "fs_write", {"path": "src/components/Board.tsx", "content": "x"}),
        owned_paths=owned,
    )
    assert ok.startswith("wrote") and meta.get("path") == "src/components/Board.tsx"
    # Out-of-ownership write is REFUSED with a corrective error (model self-corrects in-loop).
    err, meta2 = await execute_tool(
        sb, ToolCall("c2", "fs_write", {"path": "src/api/routes.py", "content": "y"}),
        owned_paths=owned,
    )
    assert meta2.get("error") == "not_owned" and "OUTSIDE your owned paths" in err
    assert not (sb.dir / "src" / "api" / "routes.py").exists()  # nothing written


async def test_fs_write_unrestricted_when_owned_paths_none(tmp_path) -> None:
    sb = await LocalSandbox.at_path(str(tmp_path / "proj2"))
    await sb.init_empty_repo()
    ok, _ = await execute_tool(
        sb, ToolCall("c1", "fs_write", {"path": "anywhere/file.py", "content": "x"}),
        owned_paths=None,  # solo build / integrator
    )
    assert ok.startswith("wrote")


# ---- parallel build wires owned_paths per worker -------------------------------------

async def test_build_parallel_enforces_ownership_per_worker(tmp_path) -> None:
    """An agent that tries to write OUTSIDE its slice is blocked; the merged result contains only
    each worker's owned files — no cross-agent collision, no stray placeholder for a sibling file."""
    import json as _json

    from app.devloop import Subtask, build_parallel
    from app.providers.base import AgentTurn
    from app.providers.base import ToolCall as TC

    class _Provider:
        """Planner: canned plan. Builder: writes its OWN file, then ATTEMPTS a sibling's file
        (which the tool must reject), then finishes."""
        name = "p"
        model = "p-1"

        def __init__(self, own: str, poach: str) -> None:
            self._own, self._poach, self._step = own, poach, 0

        async def complete(self, *, system, prompt, purpose="", max_tokens=1024):
            from app.providers.base import LLMResult
            return LLMResult(text="ok", model=self.model, tokens_in=1, tokens_out=1, cost_cents=0)

        async def complete_tools(self, *, system, messages, tools, max_tokens=2048, tool_choice="auto"):
            blob = _json.dumps(messages)
            if f"wrote {self._own}" not in blob:  # step 1: write my own file
                return AgentTurn(None, [TC("w1", "fs_write", {"path": self._own, "content": "mine\n"})], 1, 1, 0)
            if "OUTSIDE your owned paths" not in blob and f"wrote {self._poach}" not in blob:
                # step 2: try to poach the sibling's file — the tool should refuse
                return AgentTurn(None, [TC("w2", "fs_write", {"path": self._poach, "content": "poached\n"})], 1, 1, 0)
            return AgentTurn("done", [], 1, 1, 0)  # step 3: finish

    mission = type("M", (), {"key": "P-1", "id": "p1", "title": "T", "requirements": "",
                             "summary": "", "project_path": str(tmp_path / "proj")})()
    subtasks = [
        Subtask(title="API", files=["src/api.py"], instructions="build api", role="backend"),
        Subtask(title="UI", files=["src/ui.js"], instructions="build ui", role="frontend"),
    ]
    agents = [
        (_Provider(own="src/api.py", poach="src/ui.js"), "Ada", "backend"),
        (_Provider(own="src/ui.js", poach="src/api.py"), "Ivy", "frontend"),
    ]
    result, sb, _p = await build_parallel(mission, _Provider("x", "y"), agents, subtasks=subtasks)
    assert "src/api.py" in result.files and "src/ui.js" in result.files
    # Each file contains only its OWNER's content — the poach attempts were all refused.
    assert (sb.dir / "src" / "api.py").read_text() == "mine\n"
    assert (sb.dir / "src" / "ui.js").read_text() == "mine\n"
    await sb.destroy()
