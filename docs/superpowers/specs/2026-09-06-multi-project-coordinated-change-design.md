# Multi-Project Coordinated Change — Design

**Date:** 2026-09-06
**Status:** Approved design (pre-implementation)
**Approach:** A — multi-target mission (chosen over parent/child sub-missions and pseudo-monorepo)

## Problem

Today every unit of work in Shipwright is a **mission** bound to a single `project_path`.
Iterating on an existing app means reopening its mission (`request_change`) or creating a
change-mission against one repo. There is no way to select **several related projects** (e.g. a
`service` and its `gateway`) and prompt one change that spans them — so when the team adds an API
in the service, nothing updates the gateway.

## Goal

Let the user pick **one or more projects** and describe a change; the AI plans and edits **whichever
of the selected repos the change requires**, with full cross-repo context, under one coordinated
review → QA → ship gate. Deliver a **Projects** picker UI as the project-centric entry point.

Non-goals (v1): GitHub-clone-backed projects (local paths only), per-project separate gates,
automatic cross-repo dependency ordering beyond what the plan expresses, project settings beyond
name + path.

## Architecture overview

```
Projects page ──select N projects + prompt──▶ POST /projects/change
      │                                              │
      │                                     create Mission{ project_ids=[…] }  + start_run
      ▼                                              ▼
  GET /projects  ◀── auto-upsert (built) ──   RunEngine
   (registry)                                   ├─ context: repo-map of ALL selected projects → agents
                                                ├─ plan: subtasks tagged with target project
                                                ├─ build: fan out — each affected repo in its own
                                                │         worktree + fix/… branch (existing sandbox/
                                                │         parallel-build machinery)
                                                └─ ONE coordinated review → QA → ship gate
                                                          (per-repo branch/PR list)
```

The multi-repo path activates only when `mission.project_ids` has ≥1 entry; when empty, the engine
uses the existing single `project_path` flow unchanged (**backward compatible**).

## Data model

### New: `Project` (registry)

`libs/py-core/src/foundry_core/models.py` — `Project(FoundryModel)`:

| field | type | notes |
|---|---|---|
| `id` | str (ULID) | |
| `workspace_id` | str | RLS scope |
| `name` | str | display name |
| `slug` | str | derived from name |
| `path` | str | absolute local git-repo path |
| `source` | str | `built` (auto from a shipped/built mission) or `registered` (user-added) |
| `created_at` | datetime | |
| `last_activity_at` | datetime \| None | last run that touched it |

`services/orchestrator/app/db_models.py` — `ProjectRow` (table `projects`), columns matching the
model; `workspace_id` indexed; RLS ENABLE + FORCE + `ws_isolation` policy (same pattern as every
tenant table). **Alembic migration 0013** creates the table + RLS.

### Mission gains a working set

`Mission` adds `project_ids: list[str] = []` (the selected projects for a multi-target change).
`project_path` remains the "primary/home" repo and every existing single-project code path is
untouched. DB: `MissionRow.project_ids` JSON column, **migration 0014** (nullable/`'[]'` default;
model coerces `None → []` via the same validator pattern used for the notify JSON columns).

## Store interface

Both `InMemoryStore` and `PostgresStore` implement:

- `list_projects(workspace_id) -> list[Project]`
- `get_project(id_or_path, workspace_id) -> Project | None`
- `create_project(project) -> Project`
- `upsert_built_project(workspace_id, name, path) -> Project` (idempotent by `path`; `source=built`)
- `delete_project(id, workspace_id) -> None`
- `touch_project(path, workspace_id)` — bump `last_activity_at`

## API (`services/orchestrator/app/api/v1`)

- `GET /projects` — list workspace projects (built + registered), newest activity first.
- `POST /projects` — register: `{name, path}`. Validates `path` exists **and** is a git repo
  (`<path>/.git` present); 422 with a clear message otherwise.
- `DELETE /projects/{id}` — unregister (registered only; built rows may be hidden, not deleted).
- `POST /projects/change` — `{projectIds: [...], title, request}` → create a change-mission with
  `project_ids`, `project_kind=change`, `project_path=<primary = first selected>`, requirements set
  to the prompt; `start_run`; return the mission. Reuses `engine.start_run`.
- Built-project auto-upsert: call `upsert_built_project` inside the existing ship/build path where
  `project_path` is finalized, so built apps appear without registration.

Scopes tagged as elsewhere (`projects:read` / `projects:write`), enforcement deferred with the rest.

## Engine & build

`services/orchestrator/app/engine.py` + `app/devloop.py`:

1. **Resolve targets.** At run start, if `mission.project_ids` is non-empty, resolve them to
   `Project` rows → list of `(name, path)`. Missing/deleted path → **halt-for-user** (reuse the
   existing halt), never crash.
2. **Cross-repo context (`app/repomap.py`, new).** `repo_map(path, budget)` returns a compact
   string: a pruned file tree (skip `.git`, `node_modules`, `.next`, `dist`, `__pycache__`, venvs,
   lockfiles) + the contents of key files (route/API defs, `package.json`/`pyproject.toml`, README)
   truncated to a per-repo char budget. `build_context(projects, budget)` concatenates labelled maps
   for all selected projects. Best-effort: a repo that fails to map logs and is skipped.
   This context is injected into the PM/CTO reasoning prompts and the build agents' briefs so every
   agent can *see* all selected repos.
3. **Plan across repos.** The `plan` phase is told the working set and that a change may span several
   repos; each subtask carries a `project` (name/path) so the builder edits the right repo. The plan
   decides which subset of the selected projects to touch.
4. **Build fan-out.** The build phase groups subtasks by target project and, for each affected repo,
   runs the existing sandbox/parallel-build flow **scoped to that repo's path** — its own
   `LocalSandbox` + `fix/<mission>-…` branch + worktrees. Reuses `devloop.build_parallel` /
   `build_from_mission` with the per-repo `projects_root`/path already threaded through
   `_effective_projects_root`.
5. **Per-repo build facts.** `self._build_facts[mission_id]` becomes a map keyed by project path
   (files changed, tests passed, healthy) so review/QA/ground-truth gate judge the **combined**
   deliverable and never ship an empty/half change.

## Review / QA / ship

One coordinated gate over the whole change set:

- **Review:** the CTO reviews all touched repos together (context = each repo's diff).
- **QA:** the harness verifies each touched repo (its app boots / its tests pass); the combined
  verdict must pass.
- **Ship:** a single merge/ship approval. `self._merge_meta[mission_id]` becomes a **list** of
  `{repo, branch}` (one per touched repo) so the gate shows a per-repo branch/PR list and the push
  step iterates them. Notifications (email/WhatsApp/Slack) fire once for the mission.

## Web UI

- **Sidebar:** new **Projects** entry (Workspace group), between Missions and Tickets.
- **`app/dashboard/projects/page.tsx`:** grid of project cards (name, path, `built`/`registered`
  badge, last activity). **Register project** modal (name + path → `POST /projects`). Multi-select
  (card checkboxes) + a prompt textarea + **Start change** → `POST /projects/change` → redirect to
  the mission's Live Build.
- **`lib/foundry.ts`:** `Project` type, `SelectionState`, `listProjects` / `registerProject` /
  `deleteProject` / `startProjectChange`.
- Styling reuses the existing card / chip / button / modal system; no new design language.

## Error handling

- Register non-existent path or non-git dir → 422 with a clear message; UI surfaces it inline.
- Selected project's path missing at run time → halt-for-user (retryable), not a crash.
- Repo-map assembly is best-effort and failure-isolated (one bad repo logs and continues).
- A multi-target run where the plan touches zero repos → halt-for-user ("nothing to change").

## Testing

- `test_projects.py` — store CRUD + RLS isolation; register validation (git vs non-git);
  built-project auto-upsert idempotency.
- `test_projects_api.py` — `GET/POST/DELETE /projects`, `POST /projects/change` creates a
  multi-target mission and starts a run.
- `test_repomap.py` — pruning (ignored dirs excluded), key-file inclusion, budget truncation.
- `test_multi_repo_build.py` — offline (MockProvider) build fan-out edits N repos, each on its own
  branch, with per-repo build facts (mirrors the existing `test_parallel_build.py`).
- `tsc` + `next lint` for the web; manual UI smoke.

## Migrations summary

- **0013** — `projects` table + RLS.
- **0014** — `missions.project_ids` JSON column (default `[]`).

## Rollout / backward compatibility

`project_ids` empty ⇒ current single-project behavior, so existing missions, seeds, and tests are
unaffected. The Projects page and endpoints are additive. The engine multi-repo path is gated on a
non-empty working set.
