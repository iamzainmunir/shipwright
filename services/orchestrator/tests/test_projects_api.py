"""Projects REST API — register (git validation), list, delete, and start a multi-target change."""
from __future__ import annotations

from app.main import create_app
from fastapi.testclient import TestClient

client = TestClient(create_app())


def _mkrepo(tmp_path, name: str) -> str:
    d = tmp_path / name
    (d / ".git").mkdir(parents=True)
    return str(d)


def test_register_requires_existing_git_repo(tmp_path):
    # missing path → 422
    r = client.post("/api/v1/projects", json={"name": "x", "path": str(tmp_path / "nope")})
    assert r.status_code == 422
    # exists but not a git repo → 422
    plain = tmp_path / "plain"
    plain.mkdir()
    r = client.post("/api/v1/projects", json={"name": "x", "path": str(plain)})
    assert r.status_code == 422


def test_register_list_delete(tmp_path):
    repo = _mkrepo(tmp_path, "gateway")
    r = client.post("/api/v1/projects", json={"name": "Gateway", "path": repo})
    assert r.status_code == 201
    proj = r.json()
    assert proj["source"] == "registered"
    assert proj["slug"] == "gateway"
    pid = proj["id"]

    listed = client.get("/api/v1/projects").json()
    assert any(p["id"] == pid for p in listed)

    assert client.delete(f"/api/v1/projects/{pid}").status_code == 204
    assert not any(p["id"] == pid for p in client.get("/api/v1/projects").json())


def test_change_requires_at_least_one_project():
    r = client.post("/api/v1/projects/change", json={"projectIds": [], "title": "t", "request": "r"})
    assert r.status_code == 422


def test_start_change_creates_multi_target_mission(tmp_path, monkeypatch):
    class _StubEngine:
        async def start_run(self, mission, **kwargs):
            return None

    monkeypatch.setattr("app.api.v1.projects.get_engine", lambda: _StubEngine())

    svc = client.post("/api/v1/projects", json={"name": "svc", "path": _mkrepo(tmp_path, "svc")}).json()
    gw = client.post("/api/v1/projects", json={"name": "gw", "path": _mkrepo(tmp_path, "gw")}).json()

    r = client.post("/api/v1/projects/change", json={
        "projectIds": [svc["id"], gw["id"]],
        "title": "Add /orders API",
        "request": "Add an orders endpoint in svc and route it in the gateway.",
    })
    assert r.status_code == 202
    m = r.json()
    assert m["projectKind"] == "change"
    assert set(m["projectIds"]) == {svc["id"], gw["id"]}
    assert m["projectPath"] == svc["path"]  # first selected = primary/home repo
