"""Artifact file store helpers (v2 Phase 0 — plan 06 §7).

Files live on disk under the artifacts root (``~/ShipwrightArtifacts`` by default), rows in
the store carry ``path`` (relative), ``sha256``, ``mime``, and per-kind ``meta``. This
module owns the disk conventions so the QA harness / ticket system / Jira mirror all
persist evidence identically.
"""

from __future__ import annotations

import hashlib
import mimetypes
from datetime import UTC, datetime
from pathlib import Path

from foundry_core.enums import ArtifactKind
from foundry_core.ids import new_ulid
from foundry_core.models import Artifact

from .config import get_settings


def artifacts_root() -> Path:
    root = get_settings().artifacts_root.strip()
    return Path(root).expanduser() if root else Path.home() / "ShipwrightArtifacts"


def run_artifact_dir(mission_id: str, run_id: str, sub: str = "qa") -> Path:
    """The canonical evidence dir for a run: <root>/<mission>/<run>/<sub>/ (created)."""
    d = artifacts_root() / mission_id / run_id / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_artifact_path(artifact: Artifact) -> Path:
    """Absolute path for an artifact row, jailed inside the artifacts root."""
    p = (artifacts_root() / artifact.path).resolve()
    root = artifacts_root().resolve()
    if not p.is_relative_to(root):  # defense: a tampered row must not escape the root
        raise ValueError(f"artifact path escapes the artifacts root: {artifact.path}")
    return p


def make_artifact_record(
    *, workspace_id: str, mission_id: str, run_id: str, file_path: Path,
    kind: ArtifactKind, name: str | None = None, step_id: str | None = None,
    meta: dict | None = None,
) -> Artifact:
    """Build an Artifact row for a file already written under the artifacts root."""
    data = file_path.read_bytes()
    rel = file_path.resolve().relative_to(artifacts_root().resolve())
    return Artifact(
        id=new_ulid(),
        workspace_id=workspace_id,
        mission_id=mission_id,
        run_id=run_id,
        step_id=step_id,
        kind=kind,
        name=name or file_path.name,
        path=str(rel),
        mime=mimetypes.guess_type(file_path.name)[0] or "application/octet-stream",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        meta=meta or {},
        created_at=datetime.now(UTC),
    )
