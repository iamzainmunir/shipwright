"""Compact cross-repo context for multi-project changes.

Gives an agent a bounded view of each selected repo — a pruned file tree plus the contents of key
files (manifests, routes/API) — so, editing the service, it can *see* the gateway and wire the new
API into it. Best-effort and budgeted: ignored dirs are skipped, output is capped, and any read
error is swallowed (context must never fail a run).
"""
from __future__ import annotations

import os

_IGNORE_DIRS = frozenset({
    ".git", "node_modules", ".next", "dist", "build", "out", "__pycache__", ".venv", "venv",
    ".turbo", ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", ".idea", ".vscode",
    "coverage", ".cache", ".gradle",
})
_KEY_FILES = ("package.json", "pyproject.toml", "go.mod", "requirements.txt", "README.md")
_KEY_DIR_HINTS = ("route", "routes", "api", "controller", "controllers", "handler", "handlers")
_SRC_EXT = (".py", ".ts", ".tsx", ".js", ".go")
_MAX_TREE_FILES = 200


def _tree(path: str, *, max_files: int = _MAX_TREE_FILES) -> str:
    root = os.path.abspath(path)
    lines: list[str] = []
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith("."))
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if rel != ".":
            lines.append(f"{'  ' * (depth - 1)}{os.path.basename(dirpath)}/")
        for f in sorted(filenames):
            if f.startswith("."):
                continue
            lines.append(f"{'  ' * depth}{f}")
            count += 1
            if count >= max_files:
                lines.append(f"{'  ' * depth}… (truncated)")
                return "\n".join(lines)
    return "\n".join(lines)


def _key_files(path: str, budget: int) -> str:
    root = os.path.abspath(path)
    picked: list[str] = [os.path.join(root, kf) for kf in _KEY_FILES if os.path.isfile(os.path.join(root, kf))]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")]
        if any(hint in os.path.basename(dirpath).lower() for hint in _KEY_DIR_HINTS):
            picked.extend(os.path.join(dirpath, f) for f in sorted(filenames) if f.endswith(_SRC_EXT))

    chunks: list[str] = []
    used = 0
    for p in picked:
        if used >= budget:
            break
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        snippet = text[: budget - used]
        chunks.append(f"--- {os.path.relpath(p, root)} ---\n{snippet}")
        used += len(snippet)
    return "\n\n".join(chunks)


def repo_map(path: str, *, budget: int = 4000) -> str:
    """A compact map of one repo — pruned file tree + key files — capped to ~``budget`` chars."""
    try:
        tree = _tree(path)
    except OSError:
        tree = "(unreadable)"
    tree_part = tree[: budget // 2]
    files = _key_files(path, budget - len(tree_part))
    out = f"Files:\n{tree_part}"
    if files:
        out += f"\n\nKey files:\n{files}"
    return out[:budget]


def build_context(projects: list[tuple[str, str]], *, budget: int = 4000) -> str:
    """Labelled repo maps for ``(name, path)`` pairs — the cross-repo context handed to the agents."""
    parts: list[str] = []
    for name, path in projects:
        try:
            mapped = repo_map(path, budget=budget)
        except Exception:  # noqa: BLE001 - best-effort; a bad repo must never fail a run
            mapped = "(could not read)"
        parts.append(f"### Project: {name} ({path})\n{mapped}")
    return "\n\n".join(parts)
