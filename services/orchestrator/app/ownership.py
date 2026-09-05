"""Disjoint file-ownership map (v2 Phase 4 — plan 04).

The middle layer of the three-layer anti-conflict system: prompts guide, THIS validates, and
``tools.fs_write`` enforces. Each parallel subtask declares ``owned_paths``; the map is machine-
validated (no empty, no overlaps) so parallel agents work on provably disjoint file sets and merges
are conflict-free by construction. To keep disjointness PROVABLE we restrict the grammar to literal
paths and directory prefixes — NO wildcards (the reference project's own noted weakness).
"""

from __future__ import annotations


def normalize_path(p: str) -> str:
    """Repo-relative, forward-slashed, no leading ``./``, no leading/trailing slashes on files."""
    rel = str(p or "").replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.strip("/") if not rel.endswith("/") else rel.lstrip("/")


def _is_dir_prefix(entry: str) -> bool:
    return entry.endswith("/")


def remap_to_owned(rel: str, owned_paths: list[str]) -> str | None:
    """Salvage the common 'dropped the directory prefix' mistake: a worker owns ``web/index.html``
    but writes ``index.html``. If ``rel`` isn't covered yet, but its FILENAME uniquely matches exactly
    one owned FILE entry's filename, return that owned path so the write lands on the intended file
    instead of being rejected. Returns None when there's no match or it's ambiguous (>1 match) or the
    path is genuinely out of lane — so a real cross-file write is still blocked."""
    if path_matches(rel, owned_paths):
        return normalize_path(rel)
    base = normalize_path(rel).rsplit("/", 1)[-1]
    if not base:
        return None
    hits = []
    for raw in owned_paths:
        entry = normalize_path(raw)
        if not entry or _is_dir_prefix(raw) or raw.endswith(("/*", "/**")):
            continue  # only remap onto concrete FILE entries, never directory prefixes
        if entry.rsplit("/", 1)[-1] == base:
            hits.append(entry)
    return hits[0] if len(hits) == 1 else None


def path_matches(rel: str, owned_paths: list[str]) -> bool:
    """True if ``rel`` is covered by an owned entry: exact file, or inside an owned directory
    (an entry ending in ``/`` — or a bare directory name that ``rel`` sits under)."""
    r = normalize_path(rel)
    for raw in owned_paths:
        entry = normalize_path(raw)
        if not entry:
            continue
        if _is_dir_prefix(raw) or raw.endswith("/*") or raw.endswith("/**"):
            d = entry.rstrip("/").removesuffix("/*").removesuffix("/**")
            if r == d or r.startswith(d + "/"):
                return True
        elif r == entry:
            return True
        elif r.startswith(entry + "/"):  # entry is a directory the file lives under
            return True
    return False


def _covers(a: str, b: str) -> bool:
    """Does owned-entry ``a`` cover path/dir ``b`` (exact, or a's directory contains b)?"""
    a_n, b_n = normalize_path(a).rstrip("/"), normalize_path(b).rstrip("/")
    if not a_n or not b_n:
        return False
    return a_n == b_n or b_n.startswith(a_n + "/")


def find_overlaps(tasks: list[dict]) -> list[str]:
    """Pairwise disjointness check across tasks' ``owned_paths``. Returns human-readable problems
    (empty ⇒ provably disjoint). Conservative but sound for literal + directory-prefix entries."""
    problems: list[str] = []
    ids = [str(t.get("id", f"T{i+1}")) for i, t in enumerate(tasks)]
    paths = [[normalize_path(p) for p in (t.get("owned_paths") or [])] for t in tasks]
    for i in range(len(tasks)):
        for j in range(i + 1, len(tasks)):
            clash = sorted({
                f"'{a}' / '{b}'"
                for a in paths[i] for b in paths[j]
                if _covers(a, b) or _covers(b, a)
            })
            if clash:
                problems.append(f"tasks {ids[i]} and {ids[j]} both claim {', '.join(clash)}")
    return problems


def validate_ownership(tasks: list[dict], valid_roles: set[str]) -> list[str]:
    """Validate an ownership map. Returns problems (empty ⇒ valid). Checks: unique non-empty ids,
    role in the catalog, non-empty owned_paths, and pairwise-disjoint ownership."""
    problems: list[str] = []
    seen: set[str] = set()
    for i, t in enumerate(tasks):
        tid = str(t.get("id") or "").strip()
        if not tid:
            problems.append(f"task #{i+1}: missing id")
        elif tid in seen:
            problems.append(f"task {tid}: duplicate id")
        else:
            seen.add(tid)
        role = str(t.get("role") or "").strip().lower()
        if role and valid_roles and role not in valid_roles:
            problems.append(f"task {tid or i+1}: unknown role '{role}'")
        owned = t.get("owned_paths") or []
        if not isinstance(owned, list) or not owned:
            problems.append(f"task {tid or i+1}: owned_paths must be a non-empty list")
    problems.extend(find_overlaps(tasks))
    return problems
