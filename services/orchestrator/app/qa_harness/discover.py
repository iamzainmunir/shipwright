"""Endpoint discovery for the QA harness (v2 Phase 2 — API-contract checks).

Enumerate the HTTP endpoints an app exposes so :mod:`api_checks` can grade them against a contract.
Two independent sources, unioned: a *served* OpenAPI/Swagger doc (ground truth when the app is up)
and a *static* scan of the built repo (works with no server, and catches routes the doc omits).

Failure-isolated like the rest of the harness: a network error, a bad JSON doc, or an undecodable
source file is DATA, not a crash — every entry point degrades to an empty list, never raises.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

# HTTP verbs we recognise in an OpenAPI ``paths`` map (everything else there — ``parameters``,
# ``summary``, ``servers`` — is metadata, not an operation).
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"})

# Dirs that never hold first-party route source (vendored deps, VCS, build output, caches). Pruning
# them keeps the scan fast and stops a bundled framework's own routes leaking into results.
_SKIP_DIRS = frozenset({"node_modules", ".git", ".venv", "dist", "build", "__pycache__"})
_PY_EXT = frozenset({".py"})
_JS_EXT = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"})
_NEXT_ROUTE_FILES = frozenset({"route.ts", "route.js", "route.tsx", "route.jsx"})
_MAX_FILE_BYTES = 2_000_000  # skip pathologically large files — no hand-written router is this big

# FastAPI/Starlette: a decorator on any router-ish object (@app.get / @router.post / @api.delete).
# Permissive on the object name (\w+); strict on the verb + the first string arg (the path).
_FASTAPI_RE = re.compile(r"""@\w+\.(get|post|put|patch|delete|head|options)\(\s*["']([^"']+)["']""")
# Express/Node: app.get('/x', …) / router.post("/y", …). Object is exactly app or router.
_EXPRESS_RE = re.compile(r"""\b(?:app|router)\.(get|post|put|patch|delete)\(\s*["']([^"']+)["']""")
# Next.js App Router: a route.ts that exports a named verb handler, either declaration form.
_NEXT_FN_RE = re.compile(r"export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b")
_NEXT_CONST_RE = re.compile(r"export\s+const\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b")
# A Next.js dynamic segment ``[id]`` → ``{id}``; a catch-all ``[...slug]`` → ``{slug}``.
_NEXT_CATCHALL_RE = re.compile(r"\[\.\.\.(\w+)\]")


@dataclass(slots=True)
class Endpoint:
    method: str          # upper-case HTTP verb: GET/POST/PUT/PATCH/DELETE/…
    path: str            # e.g. "/api/tasks" or "/api/tasks/{id}"
    source: str = ""     # "openapi" | "fastapi" | "express" | "nextjs"


def _normalize_path(path: str) -> str:
    """Canonical form for dedup/compare: leading slash, no trailing slash (except root)."""
    p = (path or "").strip()
    if not p.startswith("/"):
        p = "/" + p
    if len(p) > 1:
        p = p.rstrip("/")
    return p or "/"


# ---- OpenAPI (served) ---------------------------------------------------------

def _parse_openapi(doc: dict) -> list[Endpoint]:
    """Flatten an OpenAPI/Swagger ``paths`` map into one Endpoint per (path, verb). Non-verb keys
    under a path are metadata and skipped."""
    out: list[Endpoint] = []
    seen: set[tuple[str, str]] = set()
    paths = doc.get("paths")
    if not isinstance(paths, dict):
        return out
    for raw_path, ops in paths.items():
        if not isinstance(ops, dict):
            continue
        for verb in ops:
            v = str(verb).upper()
            if v not in _HTTP_METHODS:
                continue
            key = (v, _normalize_path(str(raw_path)))
            if key not in seen:
                seen.add(key)
                out.append(Endpoint(v, str(raw_path), "openapi"))
    return out


async def discover_from_openapi(base_url: str, client: httpx.AsyncClient | None = None) -> list[Endpoint]:
    """Fetch the app's own API description and parse it. Tries ``/openapi.json`` → ``/swagger.json``
    → ``/api-docs``; the first that returns a 200 JSON *object* wins (even if its ``paths`` is empty —
    an app that declares no routes is a fact, not a reason to keep probing). Never raises → []."""
    own = client is None
    cl = client or httpx.AsyncClient(timeout=10.0, follow_redirects=True)
    try:
        for suffix in ("/openapi.json", "/swagger.json", "/api-docs"):
            try:
                r = await cl.get(base_url.rstrip("/") + suffix)
            except Exception:  # noqa: BLE001 — a probe error is data; try the next candidate
                continue
            if r.status_code != 200:
                continue
            try:
                doc = r.json()
            except Exception:  # noqa: BLE001 — 200 but not JSON → not the doc we want
                continue
            if isinstance(doc, dict):
                return _parse_openapi(doc)
        return []
    except Exception:  # noqa: BLE001 — the harness must never fail the run
        return []
    finally:
        if own:
            await cl.aclose()


# ---- Source scan (static) -----------------------------------------------------

def _read_text(path: Path) -> str | None:
    """Best-effort text read; a huge/binary/unreadable file is skipped, never fatal."""
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, ValueError):
        return None


def _scan_fastapi(text: str) -> list[Endpoint]:
    return [Endpoint(m.group(1).upper(), m.group(2), "fastapi") for m in _FASTAPI_RE.finditer(text)]


def _scan_express(text: str) -> list[Endpoint]:
    return [Endpoint(m.group(1).upper(), m.group(2), "express") for m in _EXPRESS_RE.finditer(text)]


def _nextjs_path(rel_parts: list[str]) -> str | None:
    """Derive the URL for a Next.js App Router ``route.*`` file from its folder path.

    The URL is the segment chain under ``app/api`` (``app`` may sit under ``src/``): dynamic
    ``[id]`` → ``{id}``, catch-all ``[...slug]`` → ``{slug}``, and route groups ``(admin)`` — a
    Next.js grouping that does NOT appear in the URL — are dropped. Returns None if the file is not
    under an ``app/api`` tree."""
    for i in range(len(rel_parts) - 1):
        if rel_parts[i] == "app" and rel_parts[i + 1] == "api":
            segs: list[str] = []
            for s in rel_parts[i + 2:-1]:  # between "api" and the route.* filename
                if s.startswith("(") and s.endswith(")"):
                    continue  # route group — organisational only, not part of the path
                s = _NEXT_CATCHALL_RE.sub(r"{\1}", s)
                s = s.replace("[", "{").replace("]", "}")
                segs.append(s)
            return "/api" + ("/" + "/".join(segs) if segs else "")
    return None


def _scan_nextjs(text: str, rel_parts: list[str]) -> list[Endpoint]:
    path = _nextjs_path(rel_parts)
    if path is None:
        return []
    verbs: set[str] = set()
    for rx in (_NEXT_FN_RE, _NEXT_CONST_RE):
        verbs.update(m.group(1).upper() for m in rx.finditer(text))
    return [Endpoint(v, path, "nextjs") for v in sorted(verbs)]


def discover_from_source(project_path: str) -> list[Endpoint]:
    """Walk the built repo and extract routes from FastAPI, Express, and Next.js source. Scanners are
    scoped by file extension so a Python decorator never masquerades as an Express call. Deduped by
    (method, normalized path). A single bad file is skipped; the scan never raises."""
    out: list[Endpoint] = []
    seen: set[tuple[str, str]] = set()
    root = Path(project_path)
    try:
        walker = os.walk(root)
    except OSError:
        return out
    for dirpath, dirnames, filenames in walker:
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]  # prune in place → don't recurse
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in _PY_EXT and ext not in _JS_EXT:
                continue
            fpath = Path(dirpath) / fn
            text = _read_text(fpath)
            if text is None:
                continue
            found: list[Endpoint] = []
            if ext in _PY_EXT:
                found = _scan_fastapi(text)
            else:
                if fn in _NEXT_ROUTE_FILES:
                    try:
                        rel = list(fpath.relative_to(root).parts)
                    except ValueError:
                        rel = []
                    if rel:
                        found.extend(_scan_nextjs(text, rel))
                found.extend(_scan_express(text))
            for ep in found:
                key = (ep.method, _normalize_path(ep.path))
                if key not in seen:
                    seen.add(key)
                    out.append(ep)
    return out


# ---- Union --------------------------------------------------------------------

async def discover_endpoints(
    project_path: str, base_url: str | None = None, client: httpx.AsyncClient | None = None,
) -> list[Endpoint]:
    """All endpoints from OpenAPI (when a ``base_url`` is served) unioned with the source scan,
    deduped by (method, normalized path). OpenAPI is added first so its richer source label wins a
    tie with a static match. Never raises → []."""
    try:
        out: list[Endpoint] = []
        seen: set[tuple[str, str]] = set()

        def _add(eps: list[Endpoint]) -> None:
            for ep in eps:
                key = (ep.method.upper(), _normalize_path(ep.path))
                if key not in seen:
                    seen.add(key)
                    out.append(ep)

        if base_url:
            _add(await discover_from_openapi(base_url, client=client))
        _add(discover_from_source(project_path))
        return out
    except Exception:  # noqa: BLE001 — discovery is advisory; never fail the run
        return []
