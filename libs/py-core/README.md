# foundry-core

Shared Python core for the Shipwright platform. Distribution **`foundry-core`**, import package
**`foundry_core`**. Depended on by `services/orchestrator` (and the runner / qa workers) via:

```toml
[tool.uv.sources]
foundry-core = { path = "../../libs/py-core", editable = true }
```

## What's inside

| Module | Contents |
|---|---|
| `foundry_core.ids` | `new_ulid() -> str` (26-char Crockford base32), `mission_key(n) -> "FND-<n>"` (Canon §8) |
| `foundry_core.enums` | `str` enums locked by Canon §13.3 and §5/§6 (`RunStatus`, `ModelTier`, `MissionStage`, `BlockerKind`, `AgentRoleKey`, …) |
| `foundry_core.models` | Pydantic v2 domain models (`Org`, `Workspace`, `User`, `Mission`, `Agent`, `Blocker`, `ModelConnection`, `Skill`, `Memory`) with camelCase JSON aliases |
| `foundry_core.config` | `CoreSettings` — shared `BaseSettings` (env prefix `SHIPWRIGHT_`) |
| `foundry_core.db` | `create_engine`, `create_session_factory`, `tenant_scope(...)` — async SQLAlchemy + RLS GUCs |

## Conventions (Canon §13)

- **JSON is camelCase, DB is snake_case.** Models validate from either form
  (`populate_by_name=True`); dump the wire form with `model.model_dump(by_alias=True)`.
- **IDs are ULIDs**; user-facing mission keys are `FND-<n>`.
- **Tenant GUCs** are `app.workspace_id` / `app.org_id`; `tenant_scope` sets them
  transaction-locally so RLS isolates every query.

## Develop

```bash
cd libs/py-core
uv sync            # resolves + installs into .venv (Python >=3.13)
uv run pytest      # run the test suite
```

## Use

```python
from foundry_core import new_ulid, mission_key, Mission, MissionStage

m = Mission(id=new_ulid(), key=mission_key(142), workspaceId="01J...",
            title="Fix surcharge scoping", stage=MissionStage.BUILDING)
m.model_dump(by_alias=True)   # -> camelCase JSON dict
```

```python
from foundry_core import create_engine, create_session_factory, tenant_scope

engine = create_engine("postgresql+psycopg://shipwright:shipwright@localhost:5432/shipwright")
Session = create_session_factory(engine)

async with Session() as session:
    async with tenant_scope(session, workspace_id, org_id):
        ...  # every query is now RLS-scoped to this tenant
```
