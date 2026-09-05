"""Project-type detection (v2 Phase 2 — plan 06 §1). Pure, no side effects."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class WebKind(StrEnum):
    STATIC_HTML = "static_html"   # index.html present, no build step
    VITE_REACT = "vite_react"     # package.json with vite/react
    NODE_SERVED = "node_served"   # other npm app
    NON_WEB = "non_web"           # python/CLI/tests only


@dataclass(slots=True)
class ProbeResult:
    kind: WebKind
    project_path: str
    index_html: str | None = None     # repo-relative path to an index.html, if any
    package_json: dict | None = None
    node_entry: str | None = None      # repo-relative server entry to run (e.g. src/server.js)

    @property
    def is_web(self) -> bool:
        return self.kind is not WebKind.NON_WEB


# Server entry files, in the server-side locations builds actually use — NEVER public/ (that's the
# client bundle). server.js is the strongest signal; then app/main/index at the root or src/.
_NODE_ENTRIES = (
    "server.js", "src/server.js", "src/backend/server.js", "backend/server.js",
    "app.js", "src/app.js", "main.js", "src/main.js", "index.js", "src/index.js",
)


def _find_node_entry(root: Path) -> str | None:
    for cand in _NODE_ENTRIES:
        if (root / cand).is_file():
            return cand
    return None


def _read_package_json(root: Path) -> dict | None:
    pj = root / "package.json"
    if not pj.is_file():
        return None
    try:
        return json.loads(pj.read_text())
    except (ValueError, OSError):
        return {}


def _find_index_html(root: Path) -> str | None:
    for cand in ("index.html", "public/index.html", "src/index.html", "dist/index.html"):
        if (root / cand).is_file():
            return cand
    return None


def probe(project_path: str) -> ProbeResult:
    """Classify a built project directory into a :class:`WebKind`. Never raises."""
    root = Path(project_path)
    if not root.is_dir():
        return ProbeResult(WebKind.NON_WEB, project_path)
    pkg = _read_package_json(root)
    index = _find_index_html(root)
    entry = _find_node_entry(root)
    if pkg is not None:
        deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
        scripts = pkg.get("scripts") or {}
        if "vite" in deps or "vite" in " ".join(str(v) for v in scripts.values()) or "react" in deps:
            return ProbeResult(WebKind.VITE_REACT, project_path, index, pkg)
        if "start" in scripts or "dev" in scripts or entry:
            return ProbeResult(WebKind.NODE_SERVED, project_path, index, pkg, entry)
        if index:
            return ProbeResult(WebKind.STATIC_HTML, project_path, index, pkg)
    # No package.json — but a real server entry (server.js/…) means it's still a Node app to RUN,
    # not a dead static tree (the exact case that served the API as 404s).
    if entry:
        return ProbeResult(WebKind.NODE_SERVED, project_path, index, pkg, entry)
    if index:
        return ProbeResult(WebKind.STATIC_HTML, project_path, index, pkg)
    return ProbeResult(WebKind.NON_WEB, project_path, None, pkg)
