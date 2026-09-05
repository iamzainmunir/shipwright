"""Health probes + the Canon §13.5 error envelope, via the Starlette TestClient."""

from __future__ import annotations

from app.main import create_app
from fastapi.testclient import TestClient

client = TestClient(create_app())


def test_healthz_ok() -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "orchestrator"}
    assert resp.headers.get("X-Request-Id")  # every response is correlatable


def test_readyz_ok() -> None:
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["service"] == "orchestrator"


def test_error_envelope_on_forced_404() -> None:
    resp = client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    # exact Canon §13.5 shape: {error:{code,message,details,requestId,retryable}}
    assert set(body) == {"error"}
    err = body["error"]
    assert set(err) == {"code", "message", "details", "requestId", "retryable"}
    assert err["code"] == "not_found"
    assert err["retryable"] is False
    assert err["requestId"] == resp.headers.get("X-Request-Id")


def test_validation_error_envelope() -> None:
    # POST without the required `title` → 422 rendered as the same envelope with issues.
    resp = client.post("/api/v1/missions", json={})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "validation_failed"
    assert isinstance(err["details"]["issues"], list) and err["details"]["issues"]


def test_missions_list_is_camelcase() -> None:
    resp = client.get("/api/v1/missions")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list) and rows
    first = rows[0]
    # camelCase wire contract (Canon §13): aliased keys present, snake_case absent.
    assert "workspaceId" in first and "createdAt" in first
    assert "workspace_id" not in first


def test_create_mission_echo() -> None:
    resp = client.post("/api/v1/missions", json={"title": "Wire the webhook ingester"})
    assert resp.status_code == 201
    created = resp.json()
    assert created["title"] == "Wire the webhook ingester"
    # Mission keys use the configured prefix (default "M-", not the Jira-looking "FND-").
    from app.config import get_settings
    assert created["key"].startswith(f"{get_settings().mission_key_prefix}-")
    assert created["stage"] == "backlog"
    assert resp.headers["Location"].endswith(created["key"])


def test_edit_and_delete_mission() -> None:
    key = client.post("/api/v1/missions", json={"title": "Draft"}).json()["key"]

    # Edit before building: title, priority, requirements.
    edited = client.patch(f"/api/v1/missions/{key}",
                          json={"title": "Renamed", "priority": "P0", "requirements": "do X"})
    assert edited.status_code == 200
    body = edited.json()
    assert body["title"] == "Renamed" and body["priority"] == "p0" and body["requirements"] == "do X"

    # Empty title is rejected.
    assert client.patch(f"/api/v1/missions/{key}", json={"title": "  "}).status_code == 422

    # Delete removes it.
    assert client.delete(f"/api/v1/missions/{key}").status_code == 204
    assert client.get(f"/api/v1/missions/{key}").status_code == 404
