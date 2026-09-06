"""Project registry — model, store CRUD, built-project upsert, RLS-safe listing."""
from __future__ import annotations

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
