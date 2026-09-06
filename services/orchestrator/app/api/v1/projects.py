"""Projects — the registry of local codebases the org builds in / edits, and the entry point for a
multi-project **coordinated change** (Approach A). camelCase JSON (Canon §13).

  * GET    /api/v1/projects          x-required-scope: projects:read
  * POST   /api/v1/projects          x-required-scope: projects:write   (register a local git repo)
  * DELETE /api/v1/projects/{id}     x-required-scope: projects:write
  * POST   /api/v1/projects/change   x-required-scope: projects:write   (multi-target change mission)
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Response, status
from foundry_core.enums import MissionStage
from foundry_core.ids import new_ulid, slugify
from foundry_core.models import FoundryModel, Mission, Project
from pydantic import Field

from ...errors import not_found, unprocessable
from ...state import get_engine, get_store
from ...store import DEMO_ORG, DEMO_WS

router = APIRouter()


class RegisterBody(FoundryModel):
    """Register an existing local git repo as a project."""

    name: str = Field(min_length=1)
    path: str = Field(min_length=1)


class ChangeBody(FoundryModel):
    """Start one coordinated change across the selected projects."""

    project_ids: list[str] = Field(default_factory=list)
    title: str = Field(min_length=1)
    request: str = Field(min_length=1)


def _validate_repo(path: str) -> str:
    """Resolve + require an existing local git repo; raise 422 otherwise."""
    p = Path(path).expanduser()
    if not p.is_dir():
        raise unprocessable(f"Path not found or not a directory: {path}")
    if not (p / ".git").exists():
        raise unprocessable(f"Not a git repository (no .git found): {path}")
    return str(p.resolve())


@router.get("/projects", description="x-required-scope: projects:read")
async def list_projects() -> list[dict]:
    projects = await get_store().list_projects(DEMO_WS)
    return [p.model_dump(by_alias=True) for p in projects]


@router.post("/projects", status_code=status.HTTP_201_CREATED,
             description="x-required-scope: projects:write")
async def register_project(body: RegisterBody) -> dict:
    store = get_store()
    resolved = _validate_repo(body.path)
    existing = await store.get_project(resolved, DEMO_WS)
    if existing is not None:  # already registered/built at this path — return it, don't duplicate
        return existing.model_dump(by_alias=True)
    project = Project(
        id=new_ulid(), workspace_id=DEMO_WS, name=body.name.strip(), slug=slugify(body.name),
        path=resolved, source="registered", created_at=datetime.now(UTC),
    )
    await store.create_project(project)
    return project.model_dump(by_alias=True)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT,
               description="x-required-scope: projects:write")
async def delete_project(project_id: str) -> Response:
    await get_store().delete_project(project_id, DEMO_WS)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/projects/change", status_code=status.HTTP_202_ACCEPTED,
             description="x-required-scope: projects:write")
async def start_project_change(body: ChangeBody) -> dict:
    """Create a multi-target change mission over the selected projects and start its run."""
    store = get_store()
    if not body.project_ids:
        raise unprocessable("Select at least one project to change.")
    projects: list[Project] = []
    for pid in body.project_ids:
        p = await store.get_project(pid, DEMO_WS)
        if p is None:
            raise not_found(f"Project not found: {pid}")
        projects.append(p)
    mission = Mission(
        id=new_ulid(), key=await store.next_mission_key(), org_id=DEMO_ORG, workspace_id=DEMO_WS,
        title=body.title.strip(), summary=body.request.strip(), requirements=body.request.strip(),
        stage=MissionStage.BACKLOG, progress=0,
        project_kind="change", project_path=projects[0].path,
        project_ids=[p.id for p in projects],
    )
    await store.add_mission(mission)
    await get_engine().start_run(mission)
    return mission.model_dump(by_alias=True)
