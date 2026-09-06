"""Local filesystem directory browser — powers the path pickers (register a project, set a mission's
repo). This is a LOCAL developer tool: it lists **directories only** (never file contents) so the UI
can navigate and pick a folder, and flags which ones are git repos. camelCase JSON (Canon §13).

  * GET /api/v1/fs/list?path=<dir>   x-required-scope: projects:read
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

router = APIRouter()


@router.get("/fs/list", description="x-required-scope: projects:read")
def list_dir(path: str = "") -> dict:  # sync: blocking fs I/O runs in FastAPI's threadpool
    """List the sub-directories of ``path`` (default: the home dir), each flagged ``isRepo`` if it
    contains a ``.git``. Hidden entries and files are omitted. Any unreadable path falls back to home."""
    base = Path(path).expanduser() if path.strip() else Path.home()
    try:
        base = base.resolve()
    except OSError:
        base = Path.home()
    if not base.is_dir():
        base = Path.home()

    entries: list[dict] = []
    try:
        for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if child.name.startswith(".") or not child.is_dir():
                continue
            try:
                is_repo = (child / ".git").exists()
            except OSError:
                is_repo = False
            entries.append({"name": child.name, "path": str(child), "isRepo": is_repo})
    except OSError:  # permission denied, etc. — return an empty listing rather than 500
        entries = []

    parent = str(base.parent) if base.parent != base else None
    return {"path": str(base), "parent": parent, "entries": entries}
