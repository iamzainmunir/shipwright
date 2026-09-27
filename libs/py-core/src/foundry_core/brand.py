"""Brand — the ONE place the backend reads the product name/tagline from.

Canonical values live in the repo-root ``brand.json`` (shared with the web app's ``lib/brand.ts``),
so a rebrand is a single-file edit. This module loads that JSON when present and falls back to inline
defaults otherwise (a packaged/containerized orchestrator may not ship the repo root), so the backend
never depends on the file existing. Deliberately generic-named — not tied to the current brand.
"""
from __future__ import annotations

import json
from pathlib import Path

# Inline fallback — kept in sync with brand.json; used when the JSON isn't on disk (e.g. a deploy image).
_DEFAULTS: dict[str, str] = {
    "name": "Shipwright",
    "tagline": "Autonomous Engineering Org",
    "projectsDirName": "ShipwrightProjects",
}


def _load() -> dict[str, str]:
    """Find and read the repo-root brand.json by walking up from this module; fall back to defaults."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "brand.json"
        try:
            if candidate.is_file():
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, dict) and str(data.get("name", "")).strip():
                    return {**_DEFAULTS, **{k: str(v) for k, v in data.items() if isinstance(v, str)}}
        except Exception:  # noqa: BLE001 — a malformed/unreadable brand file must never break startup
            break
    return dict(_DEFAULTS)


_BRAND = _load()

#: The product name (user-facing) — e.g. in chat replies, notification subjects.
BRAND_NAME: str = _BRAND["name"]
#: One-line descriptor.
TAGLINE: str = _BRAND["tagline"]
#: Folder name suggested for greenfield app builds.
PROJECTS_DIR_NAME: str = _BRAND["projectsDirName"]
