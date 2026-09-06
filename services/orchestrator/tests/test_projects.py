"""Project registry — model, store CRUD, built-project upsert, RLS-safe listing."""
from __future__ import annotations

from app.store import InMemoryStore
from foundry_core.ids import new_ulid
from foundry_core.models import Project


def test_project_model_camel_and_defaults():
    p = Project(
        id=new_ulid(), workspaceId="01JWS", name="Gateway", slug="gateway",
        path="/repos/gateway", source="registered",
    )
    d = p.model_dump(by_alias=True)
    assert d["workspaceId"] == "01JWS"
    assert d["source"] == "registered"
    assert d["lastActivityAt"] is None
    assert d["path"] == "/repos/gateway"


async def test_register_and_list_project():
    s = InMemoryStore()
    p = await s.create_project(Project(
        id=new_ulid(), workspace_id="01JWS", name="Gateway", slug="gateway",
        path="/repos/gateway", source="registered",
    ))
    got = await s.list_projects("01JWS")
    assert [x.path for x in got] == ["/repos/gateway"]
    fetched = await s.get_project("/repos/gateway", "01JWS")
    assert fetched is not None and fetched.id == p.id
    # workspace isolation: another workspace sees nothing
    assert await s.list_projects("01JOTHER") == []


async def test_upsert_built_is_idempotent_by_path():
    s = InMemoryStore()
    a = await s.upsert_built_project("01JWS", "svc", "/repos/svc")
    b = await s.upsert_built_project("01JWS", "svc-renamed", "/repos/svc")
    assert a.id == b.id
    assert a.source == "built"
    assert len(await s.list_projects("01JWS")) == 1


async def test_delete_project():
    s = InMemoryStore()
    p = await s.upsert_built_project("01JWS", "svc", "/repos/svc")
    await s.delete_project(p.id, "01JWS")
    assert await s.list_projects("01JWS") == []
