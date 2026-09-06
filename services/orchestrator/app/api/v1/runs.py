"""Runs, blockers, approvals, and the live event stream (doc 03 §9/§18, Canon §13.4).

Canonical paths:
  * POST /api/v1/missions/{key}/run            start a durable run              missions:run
  * GET  /api/v1/runs/{id}                      run status/metering             missions:read
  * GET  /api/v1/runs/{id}/steps               pipeline steps                  missions:read
  * GET  /api/v1/runs/{id}/events              console/event history           missions:read
  * GET  /api/v1/missions/{key}/events         mission event history           missions:read
  * GET  /api/v1/blockers                       open blockers (attention)       missions:read
  * POST /api/v1/blockers/{id}/resolve          resolve a blocker               missions:resolve
  * POST /api/v1/missions/{key}/approvals       decide a gate                   approvals:decide.<gate>
  * GET  /api/v1/events/stream                  SSE live stream                 missions:read
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from foundry_core.enums import (
    AgentLevel,
    AgentRoleKey,
    AgentStatus,
    ApprovalDecision,
    ConnectionStatus,
    MemoryType,
    ModelKind,
    ModelProvider,
    SkillCategory,
    SkillSource,
)
from foundry_core.ids import new_ulid
from foundry_core.models import Agent, CustomRole, Memory, ModelConnection, Skill, Team
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from ...errors import not_found, unprocessable
from ...seed import DEMO_ORG, DEMO_WS
from ...state import get_bus, get_engine, get_store

router = APIRouter(tags=["runs"])


class _Body(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class ResolveBlockerBody(_Body):
    kind: str | None = None
    decision: str | None = None  # approval: "approve" | "reject"
    action: str | None = None    # limit/token/etc: "raiseCap" | "reconnect" | ...
    answer: str | None = None    # question kind
    note: str | None = None


class ApprovalBody(_Body):
    gate: str = "merge"
    decision: str = Field(pattern="^(approve|reject)$")
    note: str | None = None
    repo: str | None = None  # "owner/name" to push the branch + open a PR at merge
    branch: str | None = None  # branch name to push (defaults to the mission's build branch)
    force: bool = False  # USER explicitly authorizing a force-push (overwrite) of the base branch


def _decision(value: str | None) -> ApprovalDecision:
    try:
        return ApprovalDecision(str(value))
    except ValueError as exc:
        raise unprocessable("decision must be 'approve' or 'reject'") from exc


def _memory_type(value: str) -> MemoryType:
    try:
        return MemoryType(value)
    except ValueError as exc:
        raise unprocessable(f"invalid memory type {value!r} (project|feedback|reference|user)") from exc


def _skill_category(value: str) -> SkillCategory:
    try:
        return SkillCategory(value)
    except ValueError as exc:
        raise unprocessable(f"invalid skill category {value!r}") from exc


# ---- runs -----------------------------------------------------------------------

@router.post("/missions/{key}/run", status_code=status.HTTP_202_ACCEPTED,
             summary="Start a run", description="x-required-scope: missions:run")
async def start_run(key: str) -> dict:
    store, engine = get_store(), get_engine()
    mission = await store.get_mission(key)
    if mission is None:
        raise not_found(f"mission {key} not found")
    run = await engine.start_run(mission)
    return run.model_dump(by_alias=True)


class ChangeRequestBody(_Body):
    request: str = Field(min_length=1)  # what to change/add now that it "shipped"


@router.post("/missions/{key}/cancel", status_code=status.HTTP_202_ACCEPTED,
             summary="Force-stop the mission's active run", description="x-required-scope: missions:run")
async def cancel_mission_run(key: str) -> dict:
    """Force-stop the currently running/blocked run. Progress + context are preserved so the mission
    can be retried (optionally on a different model)."""
    store, engine = get_store(), get_engine()
    mission = await store.get_mission(key)
    if mission is None:
        raise not_found(f"mission {key} not found")
    stopped = await engine.cancel_run(mission)
    return {"ok": True, "missionKey": key, "stopped": stopped}


class RetryBody(_Body):
    from_start: bool = False  # true = redo the whole pipeline; false = resume from checkpoint


@router.post("/missions/{key}/retry", status_code=status.HTTP_202_ACCEPTED,
             summary="Retry the mission, resuming from its checkpoint with saved context",
             description="x-required-scope: missions:run")
async def retry_mission_run(key: str, body: RetryBody) -> dict:
    """Retry preserving context (requirements + on-disk project + prior outputs). Resumes from the
    last completed phase unless ``from_start``. Change the active model first to retry on a new one."""
    store, engine = get_store(), get_engine()
    mission = await store.get_mission(key)
    if mission is None:
        raise not_found(f"mission {key} not found")
    run = await engine.retry_run(mission, from_start=body.from_start)
    return run.model_dump(by_alias=True)


@router.post("/missions/{key}/change-request", status_code=status.HTTP_202_ACCEPTED,
             summary="Reopen a shipped mission with a change request",
             description="x-required-scope: missions:run")
async def request_change(key: str, body: ChangeRequestBody) -> dict:
    """The org keeps iterating: a shipped project can be reopened with a new change request, which
    folds into the brief and starts a fresh run (building on top of the existing project)."""
    store, engine = get_store(), get_engine()
    mission = await store.get_mission(key)
    if mission is None:
        raise not_found(f"mission {key} not found")
    run = await engine.request_change(mission, body.request)
    return run.model_dump(by_alias=True)


@router.get("/runs/{run_id}", summary="Get a run", description="x-required-scope: missions:read")
async def get_run(run_id: str) -> dict:
    run = await get_store().get_run(run_id)
    if run is None:
        raise not_found(f"run {run_id} not found")
    return run.model_dump(by_alias=True)


@router.get("/missions/{key}/runs", description="x-required-scope: missions:read")
async def list_mission_runs(key: str) -> list[dict]:
    """Runs for a mission, oldest→newest — lets the Live Build page reload the latest run (and its
    open gate) instead of showing a blank console after a restart."""
    store = get_store()
    mission = await store.get_mission(key)
    if mission is None:
        raise not_found(f"mission {key} not found")
    runs = [r for r in await store.list_runs(mission.workspace_id) if r.mission_id == mission.id]
    runs.sort(key=lambda r: str(r.started_at or ""))
    return [r.model_dump(by_alias=True) for r in runs]


@router.get("/runs/{run_id}/steps", description="x-required-scope: missions:read")
async def list_steps(run_id: str) -> list[dict]:
    steps = await get_store().list_steps(run_id)
    return [s.model_dump(by_alias=True) for s in steps]


# ---- artifacts (v2: QA evidence + deliverable files) ---------------------------------
@router.get("/runs/{run_id}/artifacts", description="x-required-scope: missions:read")
async def list_run_artifacts(run_id: str) -> list[dict]:
    arts = await get_store().list_artifacts(run_id=run_id)
    return [a.model_dump(by_alias=True) for a in arts]


@router.get("/missions/{key}/artifacts", description="x-required-scope: missions:read")
async def list_mission_artifacts(key: str) -> list[dict]:
    mission = await get_store().get_mission(key)
    if mission is None:
        raise not_found(f"mission {key} not found")
    arts = await get_store().list_artifacts(mission_id=mission.id)
    return [a.model_dump(by_alias=True) for a in arts]


@router.get("/artifacts/{artifact_id}/content", description="x-required-scope: missions:read")
async def artifact_content(artifact_id: str):
    """Stream an artifact file from disk (mime from the row) — the QA tab, ticket evidence
    gallery, and Jira attacher all read through this."""
    from fastapi.responses import FileResponse

    from ...artifacts import resolve_artifact_path

    art = await get_store().get_artifact(artifact_id)
    if art is None:
        raise not_found(f"artifact {artifact_id} not found")
    try:
        path = resolve_artifact_path(art)
    except ValueError as exc:
        raise not_found(str(exc)) from exc
    if not path.is_file():
        raise not_found(f"artifact file missing on disk: {art.path}")
    return FileResponse(path, media_type=art.mime, filename=art.name)


@router.get("/runs/{run_id}/events", description="x-required-scope: missions:read")
async def list_run_events(run_id: str, limit: int = 200) -> list[dict]:
    events = await get_store().list_events(run_id=run_id, limit=limit)
    return [e.model_dump(by_alias=True) for e in events]


@router.get("/runs/{run_id}/export", description="x-required-scope: missions:read")
async def export_run(run_id: str) -> Response:
    """Download a full run bundle — mission + run + steps + events + metering — as JSON."""
    store = get_store()
    run = await store.get_run(run_id)
    if run is None:
        raise not_found(f"run {run_id} not found")
    steps = await store.list_steps(run_id)
    events = await store.list_events(run_id=run_id, limit=1000)
    mission = await store.get_mission(run.mission_id)
    bundle = {
        "exportedAt": datetime.now(UTC).isoformat(),
        "mission": mission.model_dump(by_alias=True) if mission else None,
        "run": run.model_dump(by_alias=True),
        "steps": [s.model_dump(by_alias=True) for s in steps],
        "events": [e.model_dump(by_alias=True) for e in events],
    }
    name = mission.key if mission else run_id
    return JSONResponse(
        jsonable_encoder(bundle),  # serialize datetimes/enums the way FastAPI does for route returns
        headers={"Content-Disposition": f'attachment; filename="shipwright-{name}-run.json"'},
    )


@router.get("/events/recent", description="x-required-scope: missions:read")
async def list_recent_events(limit: int = 30) -> list[dict]:
    """The workspace's most recent real events (append-only log the engine emits), newest last —
    the honest source for the Command Center activity feed."""
    events = await get_store().list_events(limit=limit)
    return [e.model_dump(by_alias=True) for e in events]


@router.get("/missions/{key}/events", description="x-required-scope: missions:read")
async def list_mission_events(key: str, limit: int = 200) -> list[dict]:
    store = get_store()
    mission = await store.get_mission(key)
    if mission is None:
        raise not_found(f"mission {key} not found")
    events = await store.list_events(mission_id=mission.id, limit=limit)
    return [e.model_dump(by_alias=True) for e in events]


# ---- blockers & approvals -------------------------------------------------------

@router.get("/blockers", description="x-required-scope: missions:read")
async def list_blockers() -> list[dict]:
    blockers = await get_store().list_blockers(unresolved_only=True)
    return [b.model_dump(by_alias=True) for b in blockers]


@router.post("/blockers/{blocker_id}/resolve", description="x-required-scope: missions:resolve")
async def resolve_blocker(blocker_id: str, body: ResolveBlockerBody) -> dict:
    store, engine = get_store(), get_engine()
    blocker = await store.get_blocker(blocker_id)
    if blocker is None:
        raise not_found(f"blocker {blocker_id} not found")
    if blocker.resolved_at is not None:
        raise unprocessable("blocker already resolved")
    # Phase 1 blockers are approval gates; default to approve if a decision is omitted.
    decision = _decision(body.decision or "approve")
    resolved = await engine.resolve_blocker(
        blocker_id, decision, actor=None, note=body.note or body.answer
    )
    return resolved.model_dump(by_alias=True)


@router.post("/missions/{key}/approvals", description="x-required-scope: approvals:decide.<gate>")
async def decide_gate(key: str, body: ApprovalBody) -> dict:
    store, engine = get_store(), get_engine()
    mission = await store.get_mission(key)
    if mission is None:
        raise not_found(f"mission {key} not found")
    open_blockers = [
        b for b in await store.list_blockers(mission.workspace_id, unresolved_only=True)
        if b.mission_id == mission.id and (b.kind == "approval")
    ]
    if not open_blockers:
        raise unprocessable(f"{key} has no open approval gate")
    decision = _decision(body.decision)
    resolved = await engine.resolve_blocker(
        open_blockers[0].id, decision, actor=None, note=body.note,
        repo=body.repo, branch=body.branch, force=body.force,
    )
    return {"ok": True, "missionKey": key, "gate": body.gate, "decision": decision.value,
            "blockerId": resolved.id}


class ClarifyBody(_Body):
    answers: list[dict] = Field(default_factory=list)  # [{question, answer}, …]


@router.post("/missions/{key}/clarify", description="x-required-scope: missions:resolve")
async def submit_clarification(key: str, body: ClarifyBody) -> dict:
    """Answer the AI's clarifying questions so a paused app build can continue."""
    store, engine = get_store(), get_engine()
    mission = await store.get_mission(key)
    if mission is None:
        raise not_found(f"mission {key} not found")
    open_q = [
        b for b in await store.list_blockers(mission.workspace_id, unresolved_only=True)
        if b.mission_id == mission.id and (b.kind == "question")
    ]
    if not open_q:
        raise unprocessable(f"{key} has no open clarification")
    resolved = await engine.submit_clarification(open_q[0].id, body.answers, actor=None)
    return {"ok": True, "missionKey": key, "blockerId": resolved.id, "answered": len(body.answers)}


# ---- reference collections (Team / Models / Skills screens) ---------------------

@router.get("/agents", description="x-required-scope: agents:read")
async def list_agents() -> list[dict]:
    return [a.model_dump(by_alias=True) for a in await get_store().list_agents()]


class AgentBody(_Body):
    name: str = Field(min_length=1)
    role_key: str = "custom"
    model_binding: str | None = None
    models: list[str] = Field(default_factory=list)  # ordered failover list
    level: str = "senior"
    skills: list[str] = Field(default_factory=list)


class AgentPatch(_Body):
    name: str | None = None
    role_key: str | None = None
    model_binding: str | None = None
    models: list[str] | None = None
    level: str | None = None
    skills: list[str] | None = None
    status: str | None = None


def _agent_enum(cls, value: str, field: str):
    try:
        return cls(value)
    except ValueError as exc:
        raise unprocessable(f"invalid {field}: {value!r}") from exc


# Built-in roles → team-taxonomy group + display label (custom roles carry their own).
_BUILTIN_GROUP = {
    "ceo": "exec", "cto": "exec", "pm": "product", "ba": "product", "designer": "product",
    "backend": "eng", "frontend": "eng", "devops": "devops", "qa": "quality", "security": "quality",
    "custom": "other",
}
_BUILTIN_LABEL = {
    "ceo": "CEO", "cto": "CTO", "pm": "Product Manager", "ba": "Business Analyst",
    "backend": "Backend Engineer", "frontend": "Frontend Engineer", "qa": "QA Engineer",
    "devops": "DevOps Engineer", "designer": "Product Designer", "security": "Security Engineer",
    "custom": "Custom Agent",
}


def _slug(label: str) -> str:
    """Role key from a label: lowercase, non-alphanumerics → hyphens (e.g. 'Product Coordinator'
    → 'product-coordinator')."""
    return "-".join("".join(c if c.isalnum() else " " for c in label.lower()).split())[:48]


async def _with_role_skills(role: str, skills: list[str]) -> list[str]:
    """A role's standard skills are LOCKED (always present, listed first); the user's extra skills
    follow. De-duped case-insensitively so removing a role skill in the UI can't drop it. Resolves
    both built-in roles and workspace CUSTOM roles."""
    from app.roles import role_skills
    locked = role_skills(role)
    if not locked:  # custom role → pull its default skills from the workspace registry
        cr = await get_store().get_custom_role(DEMO_WS, role)
        if cr is not None:
            locked = list(cr.skills)
    seen: set[str] = set()
    out: list[str] = []
    for s in [*locked, *(skills or [])]:
        s = str(s).strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


async def _validate_role(role: str) -> str:
    """Accept a built-in AgentRoleKey value OR a registered custom-role key; else 422."""
    if role in AgentRoleKey.__members__ or role in {r.value for r in AgentRoleKey}:
        return role
    if await get_store().get_custom_role(DEMO_WS, role) is not None:
        return role
    raise unprocessable(f"unknown role: {role!r}")


@router.get("/roles", description="x-required-scope: agents:read")
async def list_roles() -> dict:
    """The role catalog — locked default skills, scope, label + group per role — so the Team UI can
    show auto-assigned skills and place each role in the org taxonomy. Merges built-in + custom."""
    from app.roles import ROLE_CATALOG
    out: dict[str, dict] = {
        k: {"skills": v["skills"], "scope": v["scope"],
            "label": _BUILTIN_LABEL.get(k, k.title()), "group": _BUILTIN_GROUP.get(k, "other"),
            "custom": False}
        for k, v in ROLE_CATALOG.items()
    }
    for r in await get_store().list_custom_roles(DEMO_WS):
        out[r.key] = {"skills": r.skills, "scope": r.scope, "label": r.label,
                      "group": r.group, "custom": True}
    return out


class RoleBody(_Body):
    label: str = Field(min_length=1)
    group: str = "other"
    skills: list[str] = Field(default_factory=list)
    scope: str = ""


@router.post("/roles", status_code=status.HTTP_201_CREATED, description="x-required-scope: agents:write")
async def create_role(body: RoleBody) -> dict:
    """Define a workspace custom role (e.g. Product Coordinator) with its own default skills."""
    from app.roles import ROLE_CATALOG
    key = _slug(body.label)
    if not key:
        raise unprocessable("role name must contain letters or numbers")
    if key in ROLE_CATALOG or key in {r.value for r in AgentRoleKey}:
        raise unprocessable(f"'{body.label}' matches a built-in role — pick a different name")
    if await get_store().get_custom_role(DEMO_WS, key) is not None:
        raise unprocessable(f"a role named '{body.label}' already exists")
    role = CustomRole(
        id=new_ulid(), workspace_id=DEMO_WS, key=key, label=body.label.strip(),
        group=(body.group or "other").strip(),
        skills=[s.strip() for s in body.skills if s.strip()],
        scope=body.scope.strip(), created_at=datetime.now(UTC),
    )
    created = await get_store().add_custom_role(role)
    return created.model_dump(by_alias=True)


@router.delete("/roles/{key}", status_code=status.HTTP_204_NO_CONTENT,
               description="x-required-scope: agents:write")
async def delete_role(key: str) -> Response:
    await get_store().delete_custom_role(DEMO_WS, key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/agents", status_code=status.HTTP_201_CREATED, description="x-required-scope: agents:write")
async def create_agent(body: AgentBody) -> dict:
    role = await _validate_role(body.role_key)
    agent = Agent(
        id=new_ulid(), org_id=DEMO_ORG, workspace_id=DEMO_WS,
        name=body.name, role_key=role,
        model_binding=body.model_binding or (body.models[0] if body.models else None),
        models=body.models, level=_agent_enum(AgentLevel, body.level, "level"),
        status=AgentStatus.IDLE, skills=await _with_role_skills(role, body.skills),
    )
    created = await get_store().add_agent(agent)
    return created.model_dump(by_alias=True)


@router.patch("/agents/{agent_id}", description="x-required-scope: agents:write")
async def update_agent(agent_id: str, body: AgentPatch) -> dict:
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    for not_null in ("models", "skills"):  # explicit null on a NOT-NULL JSON column → "unchanged"
        if changes.get(not_null) is None:
            changes.pop(not_null, None)
    # Re-apply the role's LOCKED skills whenever skills or the role change, so they can't be removed.
    if "skills" in changes or "role_key" in changes:
        existing = next((a for a in await get_store().list_agents() if a.id == agent_id), None)
        existing_role = getattr(existing, "role_key", None) or "custom"  # role_key is a plain str now
        role_str = changes.get("role_key") or existing_role
        base = changes.get("skills", list(existing.skills) if existing else [])
        changes["skills"] = await _with_role_skills(role_str, base)
    if "role_key" in changes:
        changes["role_key"] = await _validate_role(changes["role_key"])
    if "level" in changes:
        changes["level"] = _agent_enum(AgentLevel, changes["level"], "level")
    if "status" in changes:
        changes["status"] = _agent_enum(AgentStatus, changes["status"], "status")
    try:
        updated = await get_store().update_agent(agent_id, **changes)
    except KeyError as exc:
        raise not_found(f"agent {agent_id} not found") from exc
    return updated.model_dump(by_alias=True)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT,
               description="x-required-scope: agents:write")
async def delete_agent(agent_id: str) -> Response:
    await get_store().delete_agent(agent_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- teams (multi-team org structure; one Accountable decision-maker per role) ---

@router.get("/teams", description="x-required-scope: agents:read")
async def list_teams() -> list[dict]:
    return [t.model_dump(by_alias=True) for t in await get_store().list_teams()]


class TeamBody(_Body):
    name: str = Field(min_length=1)
    description: str | None = None
    members: list[dict] = Field(default_factory=list)  # [{agentId, accountable}]


class TeamPatch(_Body):
    name: str | None = None
    description: str | None = None
    members: list[dict] | None = None


@router.post("/teams", status_code=status.HTTP_201_CREATED, description="x-required-scope: agents:write")
async def create_team(body: TeamBody) -> dict:
    team = Team(
        id=new_ulid(), org_id=DEMO_ORG, workspace_id=DEMO_WS,
        name=body.name, description=body.description, members=body.members,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    created = await get_store().add_team(team)
    return created.model_dump(by_alias=True)


@router.patch("/teams/{team_id}", description="x-required-scope: agents:write")
async def update_team(team_id: str, body: TeamPatch) -> dict:
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    if changes.get("members") is None:
        changes.pop("members", None)
    changes["updated_at"] = datetime.now(UTC)
    try:
        updated = await get_store().update_team(team_id, **changes)
    except KeyError as exc:
        raise not_found(f"team {team_id} not found") from exc
    return updated.model_dump(by_alias=True)


@router.delete("/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT,
               description="x-required-scope: agents:write")
async def delete_team(team_id: str) -> Response:
    await get_store().delete_team(team_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


_SECRET_CONFIG_KEYS = {"apiKey", "api_key", "apikey", "token", "secret", "password"}


def _public_conn(conn) -> dict:
    """Serialize a model connection WITHOUT its secret config values (the saved API key is
    write-only: never echoed back). A boolean ``hasApiKey`` tells the UI whether a key is stored."""
    d = conn.model_dump(by_alias=True)
    cfg = dict(d.get("config") or {})
    has_key = any(str(cfg.get(k, "")).strip() for k in _SECRET_CONFIG_KEYS)
    for k in list(cfg):
        if k in _SECRET_CONFIG_KEYS:
            cfg.pop(k, None)
    cfg["hasApiKey"] = has_key
    d["config"] = cfg
    return d


@router.get("/model-connections", description="x-required-scope: models:read")
async def list_model_connections() -> list[dict]:
    return [_public_conn(c) for c in await get_store().list_model_connections()]


class ModelConnectionBody(_Body):
    provider: str
    kind: str = "cloud"
    models: list[str] = Field(default_factory=list)
    endpoint: str | None = None
    config: dict = Field(default_factory=dict)


class ModelConnectionPatch(_Body):
    status: str | None = None
    is_primary: bool | None = None
    models: list[str] | None = None
    endpoint: str | None = None
    config: dict | None = None


@router.post("/model-connections", status_code=status.HTTP_201_CREATED,
             description="x-required-scope: models:write")
async def create_model_connection(body: ModelConnectionBody) -> dict:
    try:
        provider, kind = ModelProvider(body.provider), ModelKind(body.kind)
    except ValueError as exc:
        raise unprocessable(f"invalid provider/kind: {exc}") from exc
    conn = ModelConnection(
        id=new_ulid(), org_id=DEMO_ORG, workspace_id=DEMO_WS,
        provider=provider, kind=kind, models=body.models, endpoint=body.endpoint,
        status=ConnectionStatus.CONNECTED, config=body.config,
    )
    created = await get_store().add_model_connection(conn)
    return _public_conn(created)


@router.patch("/model-connections/{conn_id}", description="x-required-scope: models:write")
async def update_model_connection(conn_id: str, body: ModelConnectionPatch) -> dict:
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    for not_null in ("models", "config"):  # explicit null on a NOT NULL column → "unchanged"
        if changes.get(not_null) is None:
            changes.pop(not_null, None)
    if "status" in changes:
        try:
            changes["status"] = ConnectionStatus(changes["status"])
        except ValueError as exc:
            raise unprocessable(f"invalid status {changes['status']!r}") from exc
    if "config" in changes:
        # MERGE config over the existing so a patch that omits the (redacted, write-only) secret
        # keys — the UI never sees them — doesn't wipe the stored API key.
        existing = next((c for c in await get_store().list_model_connections() if c.id == conn_id), None)
        if existing is not None:
            merged = dict(existing.config or {})
            merged.update(changes["config"] or {})
            changes["config"] = merged
    try:
        updated = await get_store().update_model_connection(conn_id, **changes)
    except KeyError as exc:
        raise not_found(f"model connection {conn_id} not found") from exc
    return _public_conn(updated)


@router.delete("/model-connections/{conn_id}", status_code=status.HTTP_204_NO_CONTENT,
               description="x-required-scope: models:write")
async def delete_model_connection(conn_id: str) -> Response:
    """Remove a model connection. If it was primary, the next connected connection becomes
    primary so the engine still has an active model (else runs error until one is connected)."""
    store = get_store()
    conns = await store.list_model_connections()
    target = next((c for c in conns if c.id == conn_id), None)
    if target is None:
        raise not_found(f"model connection {conn_id} not found")
    await store.delete_model_connection(conn_id)
    if target.is_primary:
        nxt = next((c for c in conns if c.id != conn_id and c.status == ConnectionStatus.CONNECTED), None)
        if nxt is not None:
            await store.update_model_connection(nxt.id, is_primary=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _parse_reset(v: object) -> datetime | None:
    """Parse a stored ISO `usageResetAt` (UTC 'Z' or offset) into an aware datetime, else None."""
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def visible_runs() -> list:
    """All runs with per-provider 'reset usage' applied: a run is dropped when its provider's
    connection was reset (config.usageResetAt) after the run finished. Non-destructive — the runs
    stay in the store. Shared by /model-connections/usage AND /metrics so a reset is reflected
    consistently everywhere (per-model cards, spend totals, and the dashboard)."""
    store = get_store()
    runs = await store.list_runs()
    reset_at: dict[str, datetime] = {}
    for c in await store.list_model_connections():
        dt = _parse_reset((c.config or {}).get("usageResetAt"))
        if dt is not None:
            reset_at[str(getattr(c.provider, "value", c.provider)).lower()] = dt
    if not reset_at:
        return runs
    out = []
    for r in runs:
        if r.provider:
            ra = reset_at.get(r.provider.lower())
            if ra is not None:
                ts = r.finished_at or r.started_at
                if ts is not None and (ts if ts.tzinfo else ts.replace(tzinfo=UTC)) < ra:
                    continue  # usage reset after this run — drop it everywhere
        out.append(r)
    return out


@router.get("/model-connections/usage", description="x-required-scope: models:read")
async def model_connection_usage() -> dict:
    """Real per-provider usage, aggregated from what each run actually executed on (with per-provider
    'reset usage' applied). Only runs that recorded a provider are attributed; `totals` covers all."""
    runs = await visible_runs()
    providers: dict[str, dict] = {}
    totals = {"runs": 0, "tokensIn": 0, "tokensOut": 0, "costCents": 0}
    for r in runs:
        totals["runs"] += 1
        totals["tokensIn"] += r.tokens_in
        totals["tokensOut"] += r.tokens_out
        totals["costCents"] += r.cost_cents
        if not r.provider:
            continue  # unattributed (legacy) run — counted in totals only, never invented per-provider
        p = providers.setdefault(
            r.provider,
            {"runs": 0, "tokensIn": 0, "tokensOut": 0, "costCents": 0, "models": {}},
        )
        p["runs"] += 1
        p["tokensIn"] += r.tokens_in
        p["tokensOut"] += r.tokens_out
        p["costCents"] += r.cost_cents
        if r.model:
            m = p["models"].setdefault(r.model, {"runs": 0, "tokensIn": 0, "tokensOut": 0, "costCents": 0})
            m["runs"] += 1
            m["tokensIn"] += r.tokens_in
            m["tokensOut"] += r.tokens_out
            m["costCents"] += r.cost_cents
    return {"providers": providers, "totals": totals}


@router.get("/model-connections/providers", description="x-required-scope: models:read")
async def list_providers() -> list[dict]:
    """The providers the engine can run, for the connect UI: Anthropic, the OpenAI-compatible
    family (OpenAI/Gemini/Grok/Groq/Mistral/DeepSeek/Together/Cohere), and local Ollama."""
    from ...providers.catalog import OPENAI_COMPATIBLE

    out: list[dict] = [
        {"value": "anthropic", "label": "Anthropic (Claude)", "kind": "cloud", "needsKey": True,
         "defaultModels": ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"]},
        # Claude Code CLI: uses the machine's logged-in Claude subscription seat (no API key). Model
        # is an alias (auto/opus/sonnet/haiku) and effort maps to the CLI's --effort levels.
        {"value": "claude_cli", "label": "Claude CLI (your Claude Code login)", "kind": "cloud",
         "needsKey": False, "cli": True, "defaultModels": ["auto", "opus", "sonnet", "haiku"],
         "efforts": ["auto", "low", "medium", "high", "xhigh", "max"]},
    ]
    for spec in OPENAI_COMPATIBLE.values():
        out.append({"value": spec.key, "label": spec.label, "kind": "cloud", "needsKey": True,
                    "defaultModels": spec.default_models,
                    # A spec with no fixed base URL (the generic option) needs the user to enter one.
                    "needsEndpoint": not spec.base_url})
    out.append({"value": "ollama", "label": "Ollama (local)", "kind": "local", "needsKey": False,
                "defaultModels": ["qwen2.5:7b", "gemma2:2b"]})
    return out


async def _test_claude_cli() -> dict:
    """Probe the local Claude Code binary + login state (no tokens spent). Reports whether the seat
    is signed in so the connect UI can say 'ready' or 'run `claude auth login`'."""
    import asyncio
    import json as _json
    import shutil

    binary = shutil.which("claude")
    if not binary:
        return {"ok": False, "reason": "claude CLI not found",
                "detail": "install Claude Code so the `claude` binary is on PATH"}
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "auth", "status", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        # Generous: the CLI's FIRST invocation under the server can cold-start (plugin sync/prefetch).
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=25.0)
    except (TimeoutError, OSError) as exc:
        return {"ok": False, "reason": "could not run claude", "detail": str(exc)[:160]}
    try:
        status = _json.loads(out.decode(errors="replace") or "{}")
    except ValueError:
        status = {}
    if status.get("loggedIn"):
        method = status.get("authMethod") or "subscription"
        return {"ok": True, "reason": "signed in", "detail": f"Claude Code seat ({method})"}
    return {"ok": False, "reason": "signed out",
            "detail": "run `claude auth login` in a terminal to sign in with your Claude subscription seat"}


class ConnectionTestBody(_Body):
    provider: str
    model: str | None = None
    endpoint: str | None = None
    api_key: str | None = None  # transient — used only for this test, never stored


@router.post("/model-connections/test", description="x-required-scope: models:read")
async def test_model_connection(body: ConnectionTestBody) -> dict:
    """Live connectivity probe for a provider before saving a connection. Ollama lists local
    models via /api/tags; Anthropic validates the key with a 1-token message. Nothing is stored."""
    import httpx

    provider = body.provider.lower()
    if provider == "claude_cli":
        return await _test_claude_cli()
    if provider == "ollama":
        base = (body.endpoint or "http://localhost:11434").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(f"{base}/api/tags")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return {"ok": False, "reason": f"cannot reach Ollama at {base}: {exc}"}
        tags = [m.get("name", "") for m in resp.json().get("models", [])]
        if body.model and body.model not in tags:
            return {"ok": False, "reason": f"model '{body.model}' not pulled",
                    "detail": f"available: {', '.join(tags) or 'none'} — try `ollama pull {body.model}`",
                    "models": tags}
        return {"ok": True, "reason": "reachable", "detail": f"{len(tags)} model(s) available",
                "models": tags}

    if provider == "anthropic":
        if not body.api_key:
            return {"ok": False, "reason": "no API key", "detail": "enter an API key to test the connection"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": body.api_key, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": body.model or "claude-haiku-4-5", "max_tokens": 1,
                          "messages": [{"role": "user", "content": "ping"}]},
                )
        except httpx.HTTPError as exc:
            return {"ok": False, "reason": f"request failed: {exc}"}
        if resp.status_code == 200:
            return {"ok": True, "reason": "authenticated", "detail": "API key is valid"}
        if resp.status_code in (401, 403):
            return {"ok": False, "reason": "invalid API key", "detail": "authentication was rejected"}
        return {"ok": False, "reason": f"HTTP {resp.status_code}", "detail": resp.text[:200]}

    # OpenAI-compatible family (OpenAI, Google, xAI, Groq, Mistral, DeepSeek, Together, Cohere):
    # validate the key by listing models — cheap and does not spend tokens.
    from ...providers.catalog import OPENAI_COMPATIBLE

    spec = OPENAI_COMPATIBLE.get(provider)
    if spec is not None:
        key = body.api_key or os.getenv(spec.env_key, "")
        if not key:
            return {"ok": False, "reason": "no API key",
                    "detail": f"enter an API key (or set {spec.env_key}) to test the connection"}
        # A generic OpenAI-compatible provider has no fixed base URL — use the endpoint the user entered.
        base_url = spec.base_url or (body.endpoint or "").strip().rstrip("/")
        if not base_url:
            return {"ok": False, "reason": "no endpoint",
                    "detail": "enter the OpenAI-compatible base URL (e.g. https://host/v1)"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{base_url}/models",
                                        headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as exc:
            return {"ok": False, "reason": f"request failed: {exc}"}
        if resp.status_code == 200:
            try:
                ids = [m.get("id", "") for m in (resp.json().get("data") or [])]
            except ValueError:
                ids = []
            detail = f"{len(ids)} model(s) available" if ids else "API key is valid"
            return {"ok": True, "reason": "authenticated", "detail": detail, "models": ids[:50]}
        if resp.status_code in (401, 403):
            return {"ok": False, "reason": "invalid API key", "detail": "authentication was rejected"}
        return {"ok": False, "reason": f"HTTP {resp.status_code}", "detail": resp.text[:200]}

    # Any other provider is a cloud, OpenAI-compatible one — validated by its own connect flow.
    return {"ok": False, "reason": "unsupported here",
            "detail": f"connectivity test not implemented for provider '{provider}'"}


@router.get("/skills", description="x-required-scope: skills:read")
async def list_skills() -> list[dict]:
    return [s.model_dump(by_alias=True) for s in await get_store().list_skills()]


@router.get("/skills/catalog", description="x-required-scope: skills:read")
async def skills_catalog() -> list[dict]:
    """The installable skills marketplace: a curated catalog minus what's already installed
    (matched by name). Each entry can be installed via POST /skills."""
    from ...skill_catalog import CATALOG

    have = {s.name for s in await get_store().list_skills()}
    return [dict(entry, installed=entry["name"] in have) for entry in CATALOG]


class SkillBody(_Body):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    category: str = "other"
    trigger: str | None = None
    instructions: str | None = None
    auto_invoke: bool = False
    source: str | None = None  # "marketplace" for catalog installs; defaults to custom


class SkillPatch(_Body):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    trigger: str | None = None
    instructions: str | None = None
    auto_invoke: bool | None = None
    installed: bool | None = None


@router.post("/skills", status_code=status.HTTP_201_CREATED, description="x-required-scope: skills:write")
async def create_skill(body: SkillBody) -> dict:
    try:
        source = SkillSource(body.source) if body.source else SkillSource.CUSTOM
    except ValueError:
        source = SkillSource.CUSTOM
    skill = Skill(
        id=new_ulid(), org_id=DEMO_ORG, workspace_id=DEMO_WS,
        name=body.name, description=body.description, category=_skill_category(body.category),
        source=source, trigger=body.trigger, instructions=body.instructions,
        auto_invoke=body.auto_invoke, installed=True,
    )
    created = await get_store().add_skill(skill)
    return created.model_dump(by_alias=True)


@router.patch("/skills/{skill_id}", description="x-required-scope: skills:write")
async def update_skill(skill_id: str, body: SkillPatch) -> dict:
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    if "category" in changes:
        changes["category"] = _skill_category(changes["category"])
    try:
        updated = await get_store().update_skill(skill_id, **changes)
    except KeyError as exc:
        raise not_found(f"skill {skill_id} not found") from exc
    return updated.model_dump(by_alias=True)


@router.get("/memory", description="x-required-scope: memory:read")
async def list_memory() -> list[dict]:
    return [m.model_dump(by_alias=True) for m in await get_store().list_memories()]


@router.get("/memory/search", description="x-required-scope: memory:read")
async def search_memory(q: str, k: int = 5) -> list[dict]:
    """Semantic search over workspace memory — the retrieval the agents use (Phase 9)."""
    hits = await get_store().search_memories(q, k=max(1, min(k, 25)))
    return [m.model_dump(by_alias=True) for m in hits]


class MemoryBody(_Body):
    type: str = "project"
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    links: list[str] = Field(default_factory=list)


class MemoryPatch(_Body):
    type: str | None = None
    title: str | None = None
    body: str | None = None
    links: list[str] | None = None


@router.post("/memory", status_code=status.HTTP_201_CREATED, description="x-required-scope: memory:write")
async def create_memory(body: MemoryBody) -> dict:
    memory = Memory(
        id=new_ulid(), org_id=DEMO_ORG, workspace_id=DEMO_WS,
        type=_memory_type(body.type), title=body.title, body=body.body, links=body.links,
    )
    created = await get_store().add_memory(memory)
    return created.model_dump(by_alias=True)


@router.patch("/memory/{memory_id}", description="x-required-scope: memory:write")
async def update_memory(memory_id: str, body: MemoryPatch) -> dict:
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    if "type" in changes:
        changes["type"] = _memory_type(changes["type"])
    if changes.get("links") is None:
        changes.pop("links", None)  # explicit null on a NOT NULL column → treat as "unchanged"
    try:
        updated = await get_store().update_memory(memory_id, **changes)
    except KeyError as exc:
        raise not_found(f"memory {memory_id} not found") from exc
    return updated.model_dump(by_alias=True)


@router.delete("/memory/{memory_id}", status_code=status.HTTP_204_NO_CONTENT,
               description="x-required-scope: memory:write")
async def delete_memory(memory_id: str) -> Response:
    await get_store().delete_memory(memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _public_integration(i) -> dict:
    """Serialize an integration WITHOUT its stored secret (e.g. a GitHub token). The token is kept
    server-side to push on the user's behalf; the API only ever exposes a boolean ``hasToken``."""
    d = i.model_dump(by_alias=True)
    cfg = dict(d.get("config") or {})
    has_token = bool(str(cfg.get("token", "")).strip())
    for k in list(cfg):
        if k in _SECRET_CONFIG_KEYS:
            cfg.pop(k, None)
    cfg["hasToken"] = has_token
    d["config"] = cfg
    return d


@router.get("/integrations", description="x-required-scope: integrations:read")
async def list_integrations() -> list[dict]:
    return [_public_integration(i) for i in await get_store().list_integrations()]


class IntegrationConnectBody(_Body):
    credential: str | None = None  # API token / webhook secret; verified or acknowledged, not echoed


@router.post("/integrations/{kind}/connect", description="x-required-scope: integrations:write")
async def connect_integration(kind: str, body: IntegrationConnectBody | None = None) -> dict:
    """Connect an integration. Unlike a cosmetic toggle, this requires a real credential:
    Chrome is a local runtime (no key); GitHub tokens are verified live against the API; other
    providers require a credential (stored as 'provided', not live-verified in this scaffold).
    The raw secret is never persisted in or returned by the API."""
    import httpx

    store = get_store()
    existing = [i for i in await store.list_integrations() if i.kind == kind]
    if not existing:
        raise not_found(f"integration {kind} not found")
    k = kind.lower()
    cred = (body.credential if body else None) or ""

    if k == "chrome":
        config = {"runtime": "local", "verified": True}
    elif k == "github":
        if not cred:
            raise unprocessable("a GitHub token is required to connect")
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.get("https://api.github.com/user",
                                        headers={"Authorization": f"Bearer {cred}",
                                                 "Accept": "application/vnd.github+json"})
        except httpx.HTTPError as exc:
            raise unprocessable(f"could not reach GitHub: {exc}") from exc
        if resp.status_code != 200:
            raise unprocessable("GitHub rejected that token (check scopes/expiry)")
        # Persist the token so DevOps can push on the user's behalf and list their repos/branches.
        # It is stored server-side and NEVER echoed back (see _public_integration → hasToken).
        config = {"verified": True, "account": resp.json().get("login", ""), "token": cred}
    else:
        if not cred:
            raise unprocessable(f"a credential is required to connect {kind}")
        config = {"verified": False, "note": "credential provided (not live-verified in this scaffold)",
                  "token": cred}

    i = await store.set_integration_status(kind, "connected", config)
    return _public_integration(i)


@router.post("/integrations/{kind}/disconnect", description="x-required-scope: integrations:write")
async def disconnect_integration(kind: str) -> dict:
    # Drop the stored token on disconnect (don't keep a secret for a disconnected integration).
    i = await get_store().set_integration_status(kind, "disconnected", {})
    if i is None:
        raise not_found(f"integration {kind} not found")
    return _public_integration(i)


def _github_connector_from_integration(integrations):
    """Build a GitHubConnector from the connected GitHub integration's stored token (for read-only
    repo/branch listing), falling back to the server env token. Raises 422 when neither is set — the
    UI then shows 'connect GitHub first'."""
    from app.config import get_settings
    from app.connectors import GitHubConnector

    gh = next((i for i in integrations if i.kind == "github"), None)
    token = str((gh.config or {}).get("token") or "").strip() if gh else ""
    if not token:
        token = get_settings().github_token.strip()
    if not token:
        raise unprocessable("GitHub isn't connected — connect it on the Integrations page first")
    return GitHubConnector(token=token)


@router.get("/integrations/github/repos", description="x-required-scope: integrations:read")
async def list_github_repos() -> list[dict]:
    """The repos the connected GitHub token can push to (for the merge-gate repo picker)."""
    conn = _github_connector_from_integration(await get_store().list_integrations())
    try:
        return await conn.list_repos()
    except RuntimeError as exc:
        raise unprocessable(str(exc)) from exc


@router.get("/integrations/github/branches", description="x-required-scope: integrations:read")
async def list_github_branches(repo: str) -> list[str]:
    """Branch names for ``repo`` ('owner/name') via the connected GitHub token (branch picker)."""
    if "/" not in repo:
        raise unprocessable("repo must be 'owner/name'")
    owner, name = repo.split("/", 1)
    conn = _github_connector_from_integration(await get_store().list_integrations())
    try:
        return await conn.list_branches(owner, name)
    except RuntimeError as exc:
        raise unprocessable(str(exc)) from exc


class SettingsPatch(_Body):
    autonomy: str | None = None
    gates: dict[str, bool] | None = None
    spend_threshold_cents: int | None = None
    budget_cap_cents: int | None = None
    max_parallel_agents: int | None = None
    guardrails: dict[str, bool] | None = None
    mission_key_prefix: str | None = None
    projects_dir: str | None = None
    notify_enabled: bool | None = None
    notify_channels: dict[str, bool] | None = None
    notify_events: dict[str, bool] | None = None
    notify_email: str | None = None
    notify_whatsapp: str | None = None
    notify_config: dict | None = None


# Notification provider secrets are write-only over the API — redacted on read, merged on write — so
# the UI never sees a stored password/token/webhook but can tell (via a ``<key>Set`` bool) one is saved.
_NOTIFY_SECRET_KEYS = {"smtpPassword", "twilioAuthToken", "whatsappToken", "slackWebhook",
                       "slackSigningSecret", "whatsappAppSecret", "whatsappVerifyToken"}


def _public_settings(policy) -> dict:
    """Serialize settings with ``notify_config`` secrets redacted (each replaced by a ``<key>Set`` bool)."""
    d = policy.model_dump(by_alias=True)
    cfg = dict(d.get("notifyConfig") or {})
    for k in _NOTIFY_SECRET_KEYS:
        cfg[f"{k}Set"] = bool(str(cfg.get(k, "")).strip())
        cfg.pop(k, None)
    d["notifyConfig"] = cfg
    return d


@router.get("/settings", description="x-required-scope: settings:read")
async def get_settings() -> dict:
    return _public_settings(await get_store().get_settings())


@router.patch("/settings", description="x-required-scope: settings:write")
async def update_settings(body: SettingsPatch) -> dict:
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    if "notify_config" in changes:
        incoming = {k: v for k, v in (changes.get("notify_config") or {}).items()
                    if not k.endswith("Set")}  # drop the read-only markers the UI echoes back
        # a blank secret means "unchanged" — merge over the stored config so it isn't wiped
        incoming = {k: v for k, v in incoming.items()
                    if not (k in _NOTIFY_SECRET_KEYS and not str(v).strip())}
        merged = dict((await get_store().get_settings()).notify_config or {})
        merged.update(incoming)
        changes["notify_config"] = merged
    updated = await get_store().update_settings(**changes)
    return _public_settings(updated)


@router.post("/settings/notify-test", description="x-required-scope: settings:write")
async def notify_test() -> dict:
    """Send a test notification to every enabled channel (using the stored credentials)."""
    from ...notifier import Notifier
    sent = await Notifier(get_store()).send_test(DEMO_WS)
    return {"sent": sent}


# ---- workspace metrics (Command Center) -----------------------------------------

@router.get("/metrics", description="x-required-scope: missions:read")
async def workspace_metrics() -> dict:
    """Live Command-Center tiles derived from the store (single-workspace slice form of
    the canonical `GET /workspaces/{id}/metrics`, Canon §13.4)."""
    store = get_store()
    missions = await store.list_missions()
    runs = await visible_runs()  # honor per-provider 'reset usage' so the dashboard matches Models
    # "In flight" = actually moving through the pipeline (matches the dashboard's ACTIVE_STAGES),
    # not merely "not shipped" — a backlog ticket is not in flight.
    active_stages = {"spec", "building", "qa", "review"}
    active = [m for m in missions if _stage_value(m.stage) in active_stages]
    shipped = [m for m in missions if _stage_value(m.stage) == "shipped"]
    agents = await store.list_agents()
    working = [a for a in agents if _stage_value(a.status) == "working"]
    return {
        "activeMissions": len(active),
        "shipped": len(shipped),
        "agents": len(agents),
        "agentsWorking": len(working),
        "runs": len(runs),
        "spendCents": sum(r.cost_cents for r in runs),
        "tokensIn": sum(r.tokens_in for r in runs),
        "tokensOut": sum(r.tokens_out for r in runs),
        "openBlockers": len(await store.list_blockers(unresolved_only=True)),
    }


def _stage_value(value: object) -> str:
    """Normalize an enum-or-string status/stage to its string value."""
    return str(getattr(value, "value", value))


# ---- live stream (SSE) ----------------------------------------------------------

@router.get("/events/stream", summary="Live event stream (SSE)",
            description="x-required-scope: missions:read")
async def events_stream(run: str | None = None, mission: str | None = None) -> Response:
    store, bus = get_store(), get_bus()
    mission_id: str | None = None
    if mission is not None:
        m = await store.get_mission(mission)
        mission_id = m.id if m else mission

    async def gen() -> AsyncIterator[bytes]:
        yield b": stream open\n\n"
        async for ev in bus.subscribe():
            if run is not None and ev.run_id != run:
                continue
            if mission_id is not None and ev.mission_id != mission_id:
                continue
            yield f"data: {ev.model_dump_json(by_alias=True)}\n\n".encode()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
