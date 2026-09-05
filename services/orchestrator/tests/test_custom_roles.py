"""Custom (user-defined) agent roles with default skills — the /roles registry + agent assignment."""

from __future__ import annotations

from app.main import create_app
from fastapi.testclient import TestClient

client = TestClient(create_app())


def test_builtin_roles_have_label_and_group() -> None:
    roles = client.get("/api/v1/roles").json()
    assert roles["backend"]["custom"] is False
    assert roles["backend"]["group"] == "eng"
    assert roles["devops"]["group"] == "devops"  # DevOps is its own group, separate from Eng
    assert roles["pm"]["label"] == "Product Manager"
    assert roles["backend"]["skills"]  # locked defaults present


def test_custom_role_lifecycle_and_agent_assignment() -> None:
    # Create a custom role (like a real org adding "Product Coordinator") with default skills.
    r = client.post("/api/v1/roles", json={
        "label": "Product Coordinator", "group": "product",
        "skills": ["stakeholder-sync", "backlog-grooming"], "scope": "Coordinates delivery across teams",
    })
    assert r.status_code == 201
    assert r.json()["key"] == "product-coordinator"

    # It shows up in the catalog, flagged custom, with its group + label.
    roles = client.get("/api/v1/roles").json()
    pc = roles.get("product-coordinator")
    assert pc and pc["custom"] is True and pc["group"] == "product"
    assert pc["label"] == "Product Coordinator" and "stakeholder-sync" in pc["skills"]

    # An agent created with the custom role auto-gets its default skills (locked) + any extras.
    a = client.post("/api/v1/agents", json={
        "name": "Percy", "roleKey": "product-coordinator", "level": "senior", "skills": ["extra-skill"],
    })
    assert a.status_code == 201
    ag = a.json()
    assert ag["roleKey"] == "product-coordinator"
    assert {"stakeholder-sync", "backlog-grooming", "extra-skill"}.issubset(set(ag["skills"]))

    # Collisions rejected: duplicate custom, or a built-in role name.
    assert client.post("/api/v1/roles", json={"label": "Product Coordinator"}).status_code == 422
    assert client.post("/api/v1/roles", json={"label": "Backend"}).status_code == 422

    # Delete removes it from the catalog.
    assert client.delete("/api/v1/roles/product-coordinator").status_code == 204
    assert "product-coordinator" not in client.get("/api/v1/roles").json()


def test_unknown_role_on_agent_is_rejected() -> None:
    resp = client.post("/api/v1/agents", json={"name": "Ghost", "roleKey": "not-a-real-role"})
    assert resp.status_code == 422
