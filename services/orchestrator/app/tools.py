"""Agent tools (Canon §13.9) bound to a sandbox.

The Backend agent calls these via the provider's tool-use loop to make a real change: read
files, write files, run commands (tests), inspect the diff. Executors run inside the
:class:`app.sandbox.LocalSandbox`, so every effect is real and isolated.
"""

from __future__ import annotations

from .ownership import path_matches, remap_to_owned
from .providers.base import ToolCall, ToolSpec
from .sandbox import LocalSandbox

MAX_OUTPUT = 2000


def tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="fs_read",
            description="Read a UTF-8 text file from the repo. Returns its contents.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "repo-relative path"}},
                "required": ["path"],
            },
        ),
        ToolSpec(
            name="fs_write",
            description="Create or overwrite a file with the given full contents.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string", "description": "the ENTIRE new file contents"},
                },
                "required": ["path", "content"],
            },
        ),
        ToolSpec(
            name="fs_list",
            description="List files in a repo directory (recursive, repo-relative). Use to see what exists.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "repo-relative dir; '' = root"}},
            },
        ),
        ToolSpec(
            name="cmd_run",
            description="Run a command in the repo (e.g. install deps, build, tests). argv form, no shell.",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "array", "items": {"type": "string"},
                                "description": 'argv, e.g. ["python3","tests/run_tests.py"]'}
                },
                "required": ["command"],
            },
        ),
        ToolSpec(
            name="git_diff",
            description="Show the unified diff of the working tree vs the base branch.",
            input_schema={"type": "object", "properties": {}},
        ),
    ]


async def execute_tool(
    sandbox: LocalSandbox, call: ToolCall, *, owned_paths: list[str] | None = None
) -> tuple[str, dict]:
    """Run one tool call against the sandbox. Returns (result_text, meta).

    ``owned_paths`` (v2 Phase 4) enforces the ownership map at the TOOL level: when set, a
    parallel worker's ``fs_write`` PHYSICALLY cannot write outside its slice — the model gets a
    corrective error string in-loop instead of silently colliding with another agent. ``None`` =
    unrestricted (solo build, integrator). This is the load-bearing anti-conflict layer: it holds
    even when the model ignores every instruction."""
    name, inp = call.name, call.input
    try:
        if name == "fs_read":
            return await sandbox.read_file(str(inp["path"])), {}
        if name == "fs_write":
            path = str(inp.get("path", "")).strip()
            if not path or path.endswith("/"):
                return ("error: 'path' must be a FILE path (e.g. src/index.html), not a folder or "
                        "empty. Retry with a filename."), {"error": "bad_path"}
            if owned_paths is not None and not path_matches(path, owned_paths):
                # Salvage the common 'dropped the directory prefix' slip (wrote index.html, owns
                # web/index.html): remap onto the intended owned file instead of losing the write.
                remapped = remap_to_owned(path, owned_paths)
                if remapped is not None:
                    await sandbox.write_file(remapped, str(inp.get("content", "")))
                    return (f"wrote {remapped} (remapped from '{path}' — write to your exact owned "
                            f"path next time)"), {"path": remapped, "remapped_from": path}
                return (
                    f"error: '{path}' is OUTSIDE your owned paths {owned_paths}. Another agent owns "
                    "it — do NOT write it; adjust your work to stay within your files."
                ), {"error": "not_owned", "path": path}
            await sandbox.write_file(path, str(inp.get("content", "")))
            return f"wrote {path}", {"path": path}
        if name == "fs_list":
            entries = await sandbox.list_files(str(inp.get("path", "") or ""))
            return ("\n".join(entries) or "(empty)"), {"count": len(entries)}
        if name == "cmd_run":
            argv = inp["command"]
            argv = argv if isinstance(argv, list) else str(argv).split()
            res = await sandbox.run(*[str(a) for a in argv], timeout_s=60)
            body = (res.stdout + res.stderr)[:MAX_OUTPUT]
            return f"exit={res.code}\n{body}", {"exit": res.code, "argv": argv}
        if name == "git_diff":
            d = await sandbox.diff("main")
            return (d[:4000] or "(no changes)"), {}
    except FileNotFoundError:
        return f"error: file not found: {inp.get('path')}", {"error": "not_found"}
    except IsADirectoryError:
        # A clear, self-correctable message beats the raw "[Errno 21] Is a directory".
        return (f"error: '{inp.get('path')}' is a directory, not a file — write to a filename "
                "inside it (e.g. index.html)."), {"error": "is_a_directory"}
    except Exception as exc:  # surface tool errors to the model, don't crash the run
        return f"error: {exc}", {"error": str(exc)}
    return f"error: unknown tool {name}", {"error": "unknown_tool"}
