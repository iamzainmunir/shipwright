"""Missions — the canonical `/api/v1/missions` surface (Canon §5 entity, doc 03 §9).

Store-backed (Phase 1 in-memory store; Postgres-backed later — same interface). Wire form is
camelCase JSON (Canon §13) via `foundry_core.models.Mission` aliases. Priority is accepted as
`P0..P3` (client/BFF form) and stored `p0..p3` (Canon §13.3).

Scopes (doc 03 §9 / Canon §13.6) — enforced by the auth layer in a later phase:
  * GET  /api/v1/missions          x-required-scope: missions:read
  * GET  /api/v1/missions/{key}    x-required-scope: missions:read
  * POST /api/v1/missions          x-required-scope: missions:write
"""

from __future__ import annotations

import io
import json
import re
import zipfile

from fastapi import APIRouter, Response, UploadFile, status
from foundry_core.enums import AutonomyLevel, MissionSource, MissionStage, Priority
from foundry_core.ids import new_ulid
from foundry_core.models import FoundryModel, Mission
from pydantic import ConfigDict, Field
from pydantic.alias_generators import to_camel

from ...errors import not_found, unprocessable
from ...state import get_preview_manager, get_store
from ...store import DEMO_ORG, DEMO_WS

router = APIRouter(prefix="/missions", tags=["missions"])

_MAX_UPLOAD = 2_000_000  # 2 MB is plenty for a requirements doc


class CreateMissionBody(FoundryModel):
    """`POST /missions` request (doc 03 §9.1, camelCase)."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    title: str = Field(min_length=1)
    summary: str = ""
    source: MissionSource = MissionSource.MANUAL
    ext_ref: str | None = None
    priority: str = "P2"  # client/BFF form P0..P3; stored p0..p3
    autonomy: AutonomyLevel = AutonomyLevel.SUPERVISED
    repo_id: str | None = None
    labels: list[str] = Field(default_factory=list)
    # App-builder: "app" (build a new project) | "change" (edit existing) | None (legacy demo)
    project_kind: str | None = None
    project_path: str | None = None
    requirements: str | None = None
    team_id: str | None = None


def _to_priority(value: str) -> Priority:
    try:
        return Priority(value.lower())
    except ValueError as exc:
        raise unprocessable(f"invalid priority {value!r} (expected P0..P3)") from exc


def _latest_run_status(runs: list) -> str | None:
    """Status of a mission's most-recently-started run (None if it has never run). Lets the board
    route a force-stopped/failed mission into its own 'Stopped' column."""
    if not runs:
        return None
    latest = max(runs, key=lambda r: str(r.started_at or ""))
    return getattr(latest.status, "value", latest.status)


def _repo_fields(mission: Mission) -> dict:
    """Repo name + a GitHub link to see the code, for the Details rail. Prefers the mission's own PR
    URL (the exact pushed code); else the server's configured target repo."""
    from ...config import get_settings

    pr = mission.pr_url or ""
    repo = get_settings().github_repo.strip()
    m = re.match(r"https?://github\.com/([^/]+/[^/]+)", pr)
    repo_name = (m.group(1) if m else None) or (repo or None)
    repo_url = f"https://github.com/{repo_name}" if repo_name else None
    # "See the full code": the PR/branch URL if we pushed, else the repo root.
    code_url = pr or repo_url
    return {"repoName": repo_name, "repoUrl": repo_url, "codeUrl": code_url}


@router.get("", summary="List missions", description="x-required-scope: missions:read")
async def list_missions() -> list[dict]:
    store = get_store()
    missions = await store.list_missions()
    all_runs = await store.list_runs()
    by_mission: dict[str, list] = {}
    for r in all_runs:
        by_mission.setdefault(r.mission_id, []).append(r)
    out = []
    for m in missions:
        d = m.model_dump(by_alias=True)
        d["lastRunStatus"] = _latest_run_status(by_mission.get(m.id, []))
        d.update(_repo_fields(m))
        out.append(d)
    return out


@router.get("/{key}", summary="Get a mission", description="x-required-scope: missions:read")
async def get_mission(key: str) -> dict:
    store = get_store()
    mission = await store.get_mission(key)
    if mission is None:
        raise not_found(f"mission {key} not found")
    runs = [r for r in await store.list_runs(mission.workspace_id) if r.mission_id == mission.id]
    d = mission.model_dump(by_alias=True)
    d["lastRunStatus"] = _latest_run_status(runs)
    d.update(_repo_fields(mission))
    return d


_ACTIVE_STATUSES = {"running", "blocked", "queued", "paused"}


async def _has_active_run(store, mission) -> bool:
    runs = [r for r in await store.list_runs(mission.workspace_id) if r.mission_id == mission.id]
    return any((getattr(r.status, "value", r.status)) in _ACTIVE_STATUSES for r in runs)


class UpdateMissionBody(FoundryModel):
    """`PATCH /missions/{key}` — edit a mission before/between runs. All fields optional."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    title: str | None = None
    summary: str | None = None
    priority: str | None = None            # P0..P3
    autonomy: AutonomyLevel | None = None
    labels: list[str] | None = None
    project_kind: str | None = None
    project_path: str | None = None
    requirements: str | None = None
    team_id: str | None = None
    ext_ref: str | None = None


@router.patch("/{key}", summary="Edit a mission", description="x-required-scope: missions:write")
async def update_mission_ep(key: str, body: UpdateMissionBody) -> dict:
    """Modify a mission's brief/settings before you build it (or between runs). Refused while a run
    is active — stop the run first."""
    store = get_store()
    mission = await store.get_mission(key)
    if mission is None:
        raise not_found(f"mission {key} not found")
    if await _has_active_run(store, mission):
        raise unprocessable("cannot edit a mission while a run is active — stop it first")

    changes = body.model_dump(exclude_unset=True, by_alias=False)
    changes = {k: v for k, v in changes.items() if v is not None}  # PATCH: only set provided fields
    if "title" in changes and not str(changes["title"]).strip():
        raise unprocessable("title cannot be empty")
    if "priority" in changes:
        changes["priority"] = _to_priority(str(changes["priority"]))
    if changes:
        mission = await store.update_mission(mission.id, **changes)
    d = mission.model_dump(by_alias=True)
    runs = [r for r in await store.list_runs(mission.workspace_id) if r.mission_id == mission.id]
    d["lastRunStatus"] = _latest_run_status(runs)
    return d


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Delete a mission", description="x-required-scope: missions:write")
async def delete_mission_ep(key: str) -> Response:
    """Delete a mission (and its runs/steps/events/blockers). Refused while a run is active."""
    store = get_store()
    mission = await store.get_mission(key)
    if mission is None:
        raise not_found(f"mission {key} not found")
    if await _has_active_run(store, mission):
        raise unprocessable("cannot delete a mission while a run is active — stop it first")
    await store.delete_mission(mission.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a mission",
    description="x-required-scope: missions:write",
)
async def create_mission(body: CreateMissionBody, response: Response) -> dict:
    store = get_store()
    key = await store.next_mission_key()
    mission = Mission(
        id=new_ulid(),
        key=key,
        org_id=DEMO_ORG,
        workspace_id=DEMO_WS,
        title=body.title,
        summary=body.summary,
        source=body.source,
        ext_ref=body.ext_ref,
        priority=_to_priority(body.priority),
        stage=MissionStage.BACKLOG,
        autonomy=body.autonomy,
        progress=0,
        repo_id=body.repo_id,
        labels=body.labels,
        project_kind=body.project_kind,
        project_path=body.project_path,
        requirements=body.requirements,
        team_id=body.team_id,
    )
    await store.add_mission(mission)
    response.headers["Location"] = f"/api/v1/missions/{key}"
    return mission.model_dump(by_alias=True)


class ImportItem(FoundryModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)
    title: str = Field(min_length=1)
    summary: str = ""
    ext_ref: str | None = None       # e.g. the Jira key "PROJ-123"
    priority: str = "P2"


class ImportBody(FoundryModel):
    """Import external tickets (e.g. Jira issues) as missions. Either give structured ``items`` or a
    pasted ``text`` block (one issue per line: "PROJ-123 Title", or a JSON array of issues)."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)
    source: str = "jira"
    items: list[ImportItem] = Field(default_factory=list)
    text: str = ""


_ISSUE_KEY = re.compile(r"^([A-Z][A-Z0-9]+-\d+)\s*[:\-–]?\s*(.*)$")


def _parse_import_text(text: str) -> list[ImportItem]:
    """Parse pasted Jira issues — a JSON array, or one issue per line ("PROJ-123 Title")."""
    text = text.strip()
    if not text:
        return []
    try:  # a JSON export/array of issues
        data = json.loads(text)
        rows = data if isinstance(data, list) else data.get("issues", [])
        out: list[ImportItem] = []
        for r in rows:
            if isinstance(r, str):
                out.append(ImportItem(title=r.strip()))
            elif isinstance(r, dict):
                f = r.get("fields", r)
                title = (f.get("summary") or r.get("title") or r.get("summary") or "").strip()
                if title:
                    desc = f.get("description")
                    out.append(ImportItem(
                        title=title, ext_ref=r.get("key") or r.get("extRef"),
                        summary=desc[:500] if isinstance(desc, str) else "",
                    ))
        if out:
            return out
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    items: list[ImportItem] = []
    for line in text.splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if not line:
            continue
        m = _ISSUE_KEY.match(line)
        if m and m.group(2):
            items.append(ImportItem(title=m.group(2).strip(), ext_ref=m.group(1)))
        else:
            items.append(ImportItem(title=line))
    return items


@router.post("/import", status_code=status.HTTP_201_CREATED,
             summary="Import external tickets (e.g. Jira) as missions",
             description="x-required-scope: missions:write")
async def import_missions(body: ImportBody) -> dict:
    """Create a mission per imported issue (source tagged, Jira key kept as ext_ref). Real missions —
    ready to build. Paste your Jira issues, or POST structured items."""
    store = get_store()
    items = body.items or _parse_import_text(body.text)
    if not items:
        raise unprocessable("nothing to import — paste Jira issues or provide items")
    created: list[dict] = []
    for it in items[:200]:  # sane cap
        key = await store.next_mission_key()
        src = MissionSource.JIRA if body.source == "jira" else MissionSource.MANUAL
        mission = Mission(
            id=new_ulid(), key=key, org_id=DEMO_ORG, workspace_id=DEMO_WS,
            title=it.title, summary=it.summary, source=src,
            ext_ref=it.ext_ref, priority=_to_priority(it.priority), stage=MissionStage.BACKLOG,
            autonomy=AutonomyLevel.SUPERVISED, progress=0,
        )
        await store.add_mission(mission)
        created.append({"key": key, "title": it.title, "extRef": it.ext_ref})
    return {"ok": True, "imported": len(created), "missions": created}


@router.post("/extract-requirements", summary="Extract requirements text from an uploaded doc",
             description="x-required-scope: missions:write")
async def extract_requirements(file: UploadFile) -> dict:
    """Read an uploaded .md/.markdown/.txt/.docx and return its plain text, so the New Mission
    form can turn a requirements document into the mission brief."""
    data = await file.read()
    if len(data) > _MAX_UPLOAD:
        raise unprocessable(f"file too large ({len(data)} bytes; max {_MAX_UPLOAD})")
    name = (file.filename or "").lower()
    try:
        if name.endswith(".docx"):
            text = _docx_to_text(data)
        else:  # .md / .markdown / .txt / anything text-like
            text = data.decode("utf-8", errors="replace")
    except Exception as exc:  # malformed upload
        raise unprocessable(f"could not read {file.filename!r}: {exc}") from exc
    text = text.strip()
    if not text:
        raise unprocessable("no text found in the uploaded file")
    return {"filename": file.filename, "text": text, "chars": len(text)}


def _docx_to_text(data: bytes) -> str:
    """Extract paragraph text from a .docx (a zip of XML) using only the stdlib."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    lines: list[str] = []
    # One line per Word paragraph; a paragraph's text is the concatenation of its <w:t> runs.
    for para in re.split(r"</w:p>", xml):
        runs = re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, flags=re.DOTALL)
        line = "".join(runs).strip()
        if line:
            lines.append(line)
    text = "\n".join(lines)
    for enc, dec in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
        text = text.replace(enc, dec)
    return text


# ── Live preview: run a built app on a localhost port so the user can open + test it ────────────

@router.get("/{key}/preview", summary="Preview status", description="x-required-scope: missions:read")
async def get_preview(key: str) -> dict:
    """Whether a live preview of this mission's built app is running, and its URL."""
    mission = await get_store().get_mission(key)
    if mission is None:
        raise not_found(f"mission {key} not found")
    return get_preview_manager().status(mission.id) or {"running": False}


@router.post("/{key}/preview", status_code=status.HTTP_202_ACCEPTED,
             summary="Run the built app", description="x-required-scope: missions:run")
async def start_preview(key: str) -> dict:
    """Launch the built app on a free localhost port and return its URL to open in a browser."""
    from ...preview import PreviewError
    mission = await get_store().get_mission(key)
    if mission is None:
        raise not_found(f"mission {key} not found")
    try:
        return await get_preview_manager().start(mission.id, mission.project_path)
    except PreviewError as exc:
        raise unprocessable(str(exc)) from exc


@router.delete("/{key}/preview", status_code=status.HTTP_204_NO_CONTENT,
               summary="Stop the running app", description="x-required-scope: missions:run")
async def stop_preview(key: str) -> Response:
    """Stop the live preview server for this mission (frees the port)."""
    mission = await get_store().get_mission(key)
    if mission is None:
        raise not_found(f"mission {key} not found")
    await get_preview_manager().stop(mission.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
