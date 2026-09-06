# Multi-Project Coordinated Change — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user select multiple projects and prompt one change that the AI plans and edits across the affected repos, under one coordinated review/QA/ship gate, via a new Projects picker.

**Architecture:** Approach A — multi-target mission. A `Project` registry (local-git-repo-backed, hybrid: built + registered). Missions gain `project_ids`; when non-empty the engine assembles cross-repo context, fans the build out across the affected repos (reusing the sandbox/worktree machinery), and ships them under one gate. Empty `project_ids` ⇒ existing single-project behavior (backward compatible).

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy 2.0 / Alembic / pydantic v2 (`foundry_core`); Next.js 15 / React 19 / TypeScript (`@foundry/web`); PostgreSQL 16.

**Spec:** `docs/superpowers/specs/2026-09-06-multi-project-coordinated-change-design.md`

## Global Constraints

- Every tenant table gets `ENABLE` + `FORCE ROW LEVEL SECURITY` + a `ws_isolation` policy keyed on `current_setting('app.workspace_id', true)` (copy the exact SQL from `0005_teams.py`).
- Domain models derive from `FoundryModel` (camelCase aliases, `populate_by_name`, `from_attributes`). JSON columns that can be NULL on old rows use a `@field_validator(..., mode="before")` coercing `None → {}`/`[]` (pattern already in `AutonomyPolicy`).
- Store methods are `async`; both `InMemoryStore` (`app/store.py`) and `PostgresStore` (`app/pgstore.py`) implement each new method. pgstore writes go through `self._sess()` and `_col(value)`.
- Migrations are sequential: next ids are **0013** (projects) then **0014** (missions.project_ids). `down_revision` chains from `0012`.
- API JSON is camelCase; request bodies subclass `_Body` (in `runs.py`) or use pydantic with `_Body`'s alias config; dumps use `model_dump(by_alias=True)`.
- `next_mission_key()` prefix already respects the per-workspace setting; reuse `store.create_mission` / `engine.start_run` — do not hand-roll mission creation.
- Backward compatibility: never change the meaning of the existing single `project_path`; the multi-repo path is gated on `mission.project_ids` being non-empty.
- Verification commands: backend `cd services/orchestrator && uv run ruff check . && uv run pytest -q`; web typecheck `node apps/web/node_modules/typescript/bin/tsc --noEmit -p apps/web/tsconfig.json` (shell Node is v16; use the Cellar node@22 for pnpm).

---

## File Structure

- `libs/py-core/src/foundry_core/models.py` — add `Project` model; add `Mission.project_ids`.
- `services/orchestrator/app/db_models.py` — add `ProjectRow`; add `MissionRow.project_ids`.
- `services/orchestrator/migrations/versions/0013_projects.py` — new `projects` table + RLS.
- `services/orchestrator/migrations/versions/0014_mission_project_ids.py` — `missions.project_ids` JSON.
- `services/orchestrator/app/store.py` + `app/pgstore.py` — project CRUD + `upsert_built_project` + `touch_project`.
- `services/orchestrator/app/api/v1/projects.py` — new router (`/projects`, `/projects/change`).
- `services/orchestrator/app/api/v1/__init__.py` — register the projects router.
- `services/orchestrator/app/repomap.py` — `repo_map()` + `build_context()`.
- `services/orchestrator/app/engine.py` — resolve targets, inject cross-repo context, fan build across repos, per-repo build facts + merge_meta; auto-upsert built project on ship.
- `apps/web/lib/foundry.ts` — `Project` type + fetchers.
- `apps/web/app/dashboard/projects/page.tsx` — Projects page.
- `apps/web/components/shell/nav-items.ts` — add Projects nav entry.
- Tests: `services/orchestrator/tests/test_projects.py`, `test_projects_api.py`, `test_repomap.py`, `test_multi_repo_build.py`.

---

## Task 1: `Project` model + `ProjectRow` + migration 0013

**Files:**
- Modify: `libs/py-core/src/foundry_core/models.py`
- Modify: `services/orchestrator/app/db_models.py`
- Create: `services/orchestrator/migrations/versions/0013_projects.py`
- Test: `services/orchestrator/tests/test_projects.py`

**Interfaces produced:**
- `Project(FoundryModel)` fields: `id: str`, `workspace_id: str`, `name: str`, `slug: str`, `path: str`, `source: str` (`"built"|"registered"`), `created_at: datetime | None`, `last_activity_at: datetime | None`.
- `ProjectRow` table `projects`.

- [ ] **Step 1: Write the failing test** (`tests/test_projects.py`)

```python
from foundry_core.models import Project
from foundry_core.ids import new_ulid

def test_project_model_camel_and_defaults():
    p = Project(id=new_ulid(), workspaceId="01JWS", name="Gateway", slug="gateway",
                path="/repos/gateway", source="registered")
    d = p.model_dump(by_alias=True)
    assert d["workspaceId"] == "01JWS" and d["source"] == "registered"
    assert d["lastActivityAt"] is None
```

- [ ] **Step 2: Run it, expect fail** — `uv run pytest tests/test_projects.py::test_project_model_camel_and_defaults -v` → ImportError/AttributeError.

- [ ] **Step 3: Add the model** to `models.py` (after `AutonomyPolicy`):

```python
class Project(FoundryModel):
    """A codebase the org can build in or edit — a local git repo. Either auto-registered when a
    mission builds to it (``source="built"``) or added by the user (``source="registered"``)."""

    id: str
    workspace_id: str
    name: str
    slug: str
    path: str
    source: str = "registered"  # "built" | "registered"
    created_at: datetime | None = None
    last_activity_at: datetime | None = None
```

- [ ] **Step 4: Add `ProjectRow`** to `db_models.py` (mirror `TeamRow`; `workspace_id` indexed):

```python
class ProjectRow(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(160))
    path: Mapped[str] = mapped_column(String(1024))
    source: Mapped[str] = mapped_column(String(16), default="registered")
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 5: Write migration 0013** (copy the create_table + RLS shape from `0005_teams.py`; `revision="0013"`, `down_revision="0012"`): create `projects` with the columns above (+ unique index on `(workspace_id, path)`), then the ENABLE/FORCE/ws_isolation block for `projects`. `downgrade()` drops the table.

- [ ] **Step 6: Verify migration imports** — `uv run python -c "import importlib.util as u; s=u.spec_from_file_location('m','migrations/versions/0013_projects.py'); m=u.module_from_spec(s); s.loader.exec_module(m); print(m.revision, m.down_revision)"` → `0013 0012`.

- [ ] **Step 7: Run test, expect pass**, then `ruff check .`.

- [ ] **Step 8: Commit** — `feat(projects): Project model + row + migration 0013`.

---

## Task 2: Project store methods (both stores)

**Files:**
- Modify: `services/orchestrator/app/store.py`, `app/pgstore.py`, `app/state.py` (Protocol)
- Test: `services/orchestrator/tests/test_projects.py`

**Interfaces produced (on both stores):**
- `async def list_projects(workspace_id=DEMO_WS) -> list[Project]`
- `async def get_project(id_or_path: str, workspace_id=DEMO_WS) -> Project | None`
- `async def create_project(project: Project) -> Project`
- `async def upsert_built_project(workspace_id: str, name: str, path: str) -> Project` (idempotent by path)
- `async def delete_project(project_id: str, workspace_id=DEMO_WS) -> None`
- `async def touch_project(path: str, workspace_id=DEMO_WS) -> None`

- [ ] **Step 1: Write failing tests**

```python
import pytest
from app.store import InMemoryStore
from foundry_core.models import Project
from foundry_core.ids import new_ulid

async def _store():
    return InMemoryStore()

async def test_register_and_list_project():
    s = await _store()
    p = await s.create_project(Project(id=new_ulid(), workspace_id="01JWS", name="Gateway",
                                       slug="gateway", path="/repos/gateway", source="registered"))
    got = await s.list_projects("01JWS")
    assert [x.path for x in got] == ["/repos/gateway"]
    assert (await s.get_project("/repos/gateway", "01JWS")).id == p.id

async def test_upsert_built_is_idempotent_by_path():
    s = await _store()
    a = await s.upsert_built_project("01JWS", "svc", "/repos/svc")
    b = await s.upsert_built_project("01JWS", "svc-renamed", "/repos/svc")
    assert a.id == b.id and len(await s.list_projects("01JWS")) == 1
    assert a.source == "built"
```

(mark the module `pytestmark = pytest.mark.asyncio` or rely on `asyncio_mode=auto`.)

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement in `InMemoryStore`** — a `self.projects: dict[str, Project]` dict (init in `__init__`), methods filtering by `workspace_id`; `upsert_built_project` finds by `(workspace_id, path)` else creates with `source="built"`, `created_at=_now()`; `slug` via a local slugify (lowercase, non-alnum→`-`). `touch_project` sets `last_activity_at`.

- [ ] **Step 4: Implement in `PostgresStore`** — mirror `list_teams`/`create_team`/`_update`: `select(ProjectRow).where(ProjectRow.workspace_id==ws)`; `create_project` adds a `ProjectRow(**project.model_dump())`; `upsert_built_project` `select(...).where(workspace_id==ws, path==path)` then create-if-missing; `delete_project` `session.delete`; `touch_project` set `last_activity_at=_now()`. Import `ProjectRow`. Add `projects` to the pgstore RLS-tables list if it maintains one at runtime (check `setup()`); the migration already sets RLS, so runtime create is not needed.

- [ ] **Step 5: Add the 6 methods to the `Store` Protocol** in `state.py`.

- [ ] **Step 6: Run tests + ruff, expect pass.**

- [ ] **Step 7: Commit** — `feat(projects): store CRUD + built-project upsert`.

---

## Task 3: Projects API (`/projects`)

**Files:**
- Create: `services/orchestrator/app/api/v1/projects.py`
- Modify: `services/orchestrator/app/api/v1/__init__.py` (include the router)
- Test: `services/orchestrator/tests/test_projects_api.py`

**Interfaces produced:**
- `GET /api/v1/projects` → `list[Project]` (camelCase).
- `POST /api/v1/projects` `{name, path}` → `Project` (422 if path missing or not a git repo).
- `DELETE /api/v1/projects/{id}` → `{ok: true}`.

- [ ] **Step 1: Write failing tests** (Starlette `TestClient`, mirror `test_health.py`): register a tmp git dir → 200 + `source=="registered"`; register a non-git tmp dir → 422; register a missing path → 422; `GET /projects` lists it; `DELETE` removes it.

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement the router.** `APIRouter()`; a `_RegisterBody(_Body)` with `name: str`, `path: str`. Validation helper:

```python
from pathlib import Path
def _validate_repo(path: str) -> str:
    p = Path(path).expanduser()
    if not p.is_dir():
        raise ApiError.unprocessable(f"Path not found: {path}")
    if not (p / ".git").exists():
        raise ApiError.unprocessable(f"Not a git repo (no .git): {path}")
    return str(p.resolve())
```

(use the existing `ApiError` helper set in `app/errors.py`; match its actual constructor — grep `class ApiError`.) `POST` builds a `Project(id=new_ulid(), workspace_id=DEMO_WS, name=..., slug=slug(name), path=_validate_repo(path), source="registered", created_at=_now())` and calls `store.create_project`. Register the router in `api/v1/__init__.py` next to the others.

- [ ] **Step 4: Run tests + ruff, expect pass.**

- [ ] **Step 5: Commit** — `feat(projects): REST API (list/register/delete)`.

---

## Task 4: `Mission.project_ids` (multi-target) + migration 0014

**Files:**
- Modify: `models.py` (Mission), `db_models.py` (MissionRow)
- Create: `migrations/versions/0014_mission_project_ids.py`
- Test: `tests/test_projects.py`

**Interfaces produced:** `Mission.project_ids: list[str]` (default `[]`).

- [ ] **Step 1: Failing test** — construct a `Mission` with `projectIds=["a","b"]`, assert `model_dump(by_alias=True)["projectIds"] == ["a","b"]`; construct one from a row-like object with `project_ids=None`, assert it coerces to `[]`.

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Add to `Mission`**: `project_ids: list[str] = Field(default_factory=list)` and a `@field_validator("project_ids", mode="before")` returning `v or []`.

- [ ] **Step 4: Add `MissionRow.project_ids`**: `Mapped[list] = mapped_column(JSON, default=list)`.

- [ ] **Step 5: Migration 0014** (`revision="0014"`, `down_revision="0013"`): `op.add_column("missions", sa.Column("project_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))`; downgrade drops it.

- [ ] **Step 6: Verify import + run tests + ruff.**

- [ ] **Step 7: Commit** — `feat(missions): project_ids working set + migration 0014`.

---

## Task 5: `POST /projects/change` + built-project auto-upsert

**Files:**
- Modify: `app/api/v1/projects.py`, `app/engine.py`
- Test: `tests/test_projects_api.py`

**Interfaces produced:** `POST /api/v1/projects/change` `{projectIds: list[str], title: str, request: str}` → `Mission` (created + run started).

- [ ] **Step 1: Failing test** — register two tmp git repos, `POST /projects/change` with both ids + a request → 202/200, response mission has `projectIds` of length 2, `projectKind=="change"`, `stage` advanced past backlog (a run started). Use MockProvider (default offline).

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement.** `_ChangeBody(_Body)`: `project_ids: list[str]`, `title: str`, `request: str`. Handler: resolve each id via `store.get_project`; 422 if any missing. Build a `Mission(id=new_ulid(), key=await store.next_mission_key(), org_id=DEMO_ORG, workspace_id=DEMO_WS, title=title, summary=request, requirements=request, project_kind="change", project_path=<first project's path>, project_ids=[p.id for p in projects], stage=MissionStage.BACKLOG, created_at=_now())`; `await store.create_mission(mission)`; `run = await get_engine().start_run(mission)`; return the mission dump. (Match `create_mission`'s real signature — grep it in `store.py`.)

- [ ] **Step 4: Built-project auto-upsert.** In `engine.py`, in the ship path (right after `shipped = await self.store.update_mission(mission_id, stage=MissionStage.SHIPPED, ...)` — see `_effective_projects_root` neighbourhood / the ship block) and in the greenfield "record project folder" block (where `planned` path is set), call `await self.store.upsert_built_project(mission.workspace_id, name=mission.title[:60], path=<path>)` guarded by `if path:` and wrapped so a failure never breaks the run (`try/except` + log).

- [ ] **Step 5: Run tests + ruff, expect pass.**

- [ ] **Step 6: Commit** — `feat(projects): change endpoint + built-project auto-upsert`.

---

## Task 6: `repomap.py` — cross-repo context

**Files:**
- Create: `services/orchestrator/app/repomap.py`
- Test: `services/orchestrator/tests/test_repomap.py`

**Interfaces produced:**
- `def repo_map(path: str, *, budget: int = 4000) -> str` — pruned tree + key files, ≤ budget chars.
- `def build_context(projects: list[tuple[str, str]], *, budget: int = 4000) -> str` — labelled maps for `(name, path)` pairs.

- [ ] **Step 1: Failing tests** — create a tmp dir with `src/app.py`, `package.json`, `node_modules/junk.js`, `.git/x`; assert `repo_map` includes `src/app.py` and `package.json`, **excludes** `node_modules` and `.git`; assert output length ≤ budget; `build_context([("svc", d1),("gw", d2)])` contains both labels.

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement.** `_IGNORE = {".git","node_modules",".next","dist","build","__pycache__",".venv","venv",".turbo",".mypy_cache",".pytest_cache"}`; walk with `os.walk`, prune ignored dirs in-place, collect relative file paths (cap count), render an indented tree; then inline the contents of key files if present (`package.json`, `pyproject.toml`, `README.md`, and any file under `**/routes`/`**/api` matching `*.py|*.ts` up to N), each truncated, total ≤ budget. All file reads in `try/except` (best-effort). `build_context` joins `f"### Project: {name} ({path})\n{repo_map(path, budget=...)}"`.

- [ ] **Step 4: Run tests + ruff, expect pass.**

- [ ] **Step 5: Commit** — `feat(engine): repo-map cross-project context`.

---

## Task 7: Engine — resolve targets + inject context

**Files:**
- Modify: `services/orchestrator/app/engine.py`
- Test: `services/orchestrator/tests/test_multi_repo_build.py` (context part)

**Interfaces produced:** `async def _resolve_targets(self, mission) -> list[tuple[str,str]]` (name, path) and `async def _project_context(self, mission) -> str` (empty string when `project_ids` empty).

- [ ] **Step 1: Failing test** — build an engine on `InMemoryStore` with two registered projects and a mission carrying both `project_ids`; assert `await engine._project_context(mission)` contains both project names; assert a mission with empty `project_ids` returns `""`.

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement.** `_resolve_targets`: for each id in `mission.project_ids`, `get_project`; if any resolves to a missing on-disk path, return a sentinel that the caller turns into a halt-for-user (reuse the existing halt method — grep the `_HALT` helper that emits "needs your attention"). `_project_context`: `build_context([(p.name, p.path) for p in resolved])` when non-empty else `""`. Inject `_project_context` into the reasoning/build briefs: find where the build brief / agent instructions are assembled (grep `requirements or summary` in the build phase) and prepend the context under a clear header when non-empty.

- [ ] **Step 4: Run tests + ruff, expect pass.**

- [ ] **Step 5: Commit** — `feat(engine): resolve multi-project targets + inject context`.

---

## Task 8: Engine — build fan-out across repos

**Files:**
- Modify: `services/orchestrator/app/engine.py`
- Test: `services/orchestrator/tests/test_multi_repo_build.py`

**Interfaces produced:** build touches each affected repo on its own branch; `self._build_facts[mission_id]` becomes `{path: {files, tests_passed, healthy}}` when multi-target.

- [ ] **Step 1: Failing test** (offline, mirror `test_parallel_build.py`) — a mission with two `project_ids` (two tmp git repos) run through the build phase with a MockProvider that writes a file in each; assert BOTH repos got a `fix/…` branch with a commit, and `engine._build_facts[mission_id]` has an entry per path.

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement.** In the build phase (`_run_build_phase`/the `is_app`/`do_parallel` block near the current `build_parallel`/`build_from_mission` calls): when `mission.project_ids` is non-empty, iterate the resolved target paths; for each, call the existing per-repo build (`devloop.build_from_mission`/`build_parallel`) with `projects_root`/path scoped to that repo (each repo is an existing git dir → change-mode edit on `fix/…`), passing the shared `_project_context`. Accumulate results into `self._build_facts[mission_id][path]`. Keep the single-project path unchanged when `project_ids` is empty. (Subtask→project routing: if the plan tagged subtasks with a project, group by it; otherwise run the whole brief against each affected repo — for v1 the plan's brief carries the cross-repo intent and each repo's agent edits only what applies.)

- [ ] **Step 4: Run tests + ruff, expect pass.**

- [ ] **Step 5: Commit** — `feat(engine): fan build out across selected repos`.

---

## Task 9: Coordinated review / ship (per-repo branches)

**Files:**
- Modify: `services/orchestrator/app/engine.py`
- Test: `services/orchestrator/tests/test_multi_repo_build.py`

- [ ] **Step 1: Failing test** — after a multi-repo build, the ship/merge metadata (`self._merge_meta[mission_id]`) lists one `{repo, branch}` per touched repo; the review context references both repos' changes.

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement.** Make `self._merge_meta[mission_id]` tolerate a list of `{repo, branch}` (one per touched repo) when multi-target; the ship/push step iterates them (grep the current single push at the ship gate and loop it). The review phase's diff context concatenates each repo's diff (from `_build_facts`). The ground-truth gate passes only if every touched repo's facts are healthy. Notifications already fire once for the mission (no change).

- [ ] **Step 4: Run tests + ruff + full suite, expect pass.**

- [ ] **Step 5: Commit** — `feat(engine): one coordinated review/ship over multi-repo change`.

---

## Task 10: Web — types + fetchers

**Files:**
- Modify: `apps/web/lib/foundry.ts`
- Test: `tsc` (types compile)

**Interfaces produced:**
- `interface Project { id; workspaceId; name; slug; path; source; createdAt; lastActivityAt }`
- `listProjects()`, `registerProject({name,path})`, `deleteProject(id)`, `startProjectChange({projectIds,title,request})`.

- [ ] **Step 1:** Add the `Project` interface + fetchers (mirror `getSettings`/`updateModelConnection` shapes; endpoints `${V1}/projects`, `${V1}/projects/change`).
- [ ] **Step 2:** `tsc --noEmit` → clean.
- [ ] **Step 3: Commit** — `feat(web): project types + api client`.

---

## Task 11: Web — Projects page + nav

**Files:**
- Create: `apps/web/app/dashboard/projects/page.tsx`
- Modify: `apps/web/components/shell/nav-items.ts`
- Test: `tsc` + manual smoke

- [ ] **Step 1:** Add a **Projects** entry to `nav-items.ts` (Workspace group, between Missions and Tickets; pick an existing IconName, e.g. `"folder"`/`"box"` — grep valid names).
- [ ] **Step 2:** Build `projects/page.tsx` (client component): `listProjects()` on mount; grid of cards (name, path, `built`/`registered` badge, last activity); a **Register project** modal (name+path → `registerProject`, surface 422 message); card checkboxes for multi-select; a prompt `<textarea>` + **Start change** button → `startProjectChange({projectIds, title, request})` → `router.push('/dashboard/missions/'+mission.key)`. Reuse existing card/badge/button/modal classes.
- [ ] **Step 3:** `tsc --noEmit` clean; start servers, register a repo, start a change, confirm redirect to Live Build.
- [ ] **Step 4: Commit** — `feat(web): Projects picker page + nav`.

---

## Task 12: Full verification

- [ ] `cd services/orchestrator && uv run ruff check . && uv run pytest -q` → all pass.
- [ ] `node apps/web/node_modules/typescript/bin/tsc --noEmit -p apps/web/tsconfig.json` → 0 errors.
- [ ] Migration gate: `uv run python -c` importing 0013/0014 heads chain 0012→0013→0014.
- [ ] Manual: register two local repos, prompt a cross-cutting change, watch both get branches, one review/ship.
- [ ] Commit any fixes; push.

---

## Self-Review notes

- **Spec coverage:** registry (T1–T3), multi-target mission (T4–T5), cross-repo context (T6–T7), build fan-out (T8), coordinated review/ship (T9), UI (T10–T11), tests throughout, migrations 0013/0014. All spec sections mapped.
- **Type consistency:** `Project`/`project_ids`/`upsert_built_project`/`build_context`/`_project_context`/`_resolve_targets` used identically across tasks.
- **Known grep-before-edit points (engine internals vary):** the build-phase block, the ship/push step, the halt-for-user helper, `create_mission` signature, and `ApiError` constructor — each task says to grep the exact site before editing rather than assuming line numbers.
