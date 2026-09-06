"""Notification credentials live in the DB (configured in the UI): secrets are redacted on read,
merged on write (a blank secret means "unchanged"), and the notifier prefers DB over env."""
from __future__ import annotations

from types import SimpleNamespace

from app.main import create_app
from app.notifier import _cfg
from fastapi.testclient import TestClient

client = TestClient(create_app())


def test_secret_redacted_on_read_and_merged_on_write():
    r = client.patch("/api/v1/settings", json={
        "notifyConfig": {"slackWebhook": "https://hooks.slack.com/xxx", "smtpHost": "smtp.example.com"},
    })
    assert r.status_code == 200
    cfg = r.json()["notifyConfig"]
    assert cfg.get("slackWebhook") is None          # secret never echoed back
    assert cfg.get("slackWebhookSet") is True        # but the UI can tell one is saved
    assert cfg.get("smtpHost") == "smtp.example.com"  # non-secret is echoed

    # a later patch that omits the secret must NOT wipe it
    r2 = client.patch("/api/v1/settings", json={"notifyConfig": {"smtpHost": "smtp2.example.com"}})
    cfg2 = r2.json()["notifyConfig"]
    assert cfg2["slackWebhookSet"] is True
    assert cfg2["smtpHost"] == "smtp2.example.com"


def test_blank_secret_does_not_wipe():
    client.patch("/api/v1/settings", json={"notifyConfig": {"slackWebhook": "https://hooks.slack.com/keep"}})
    client.patch("/api/v1/settings", json={"notifyConfig": {"slackWebhook": ""}})  # blank = unchanged
    assert client.get("/api/v1/settings").json()["notifyConfig"]["slackWebhookSet"] is True


def test_cfg_prefers_db_over_env(monkeypatch):
    monkeypatch.setenv("SHIPWRIGHT_SLACK_WEBHOOK", "https://env-webhook")
    assert _cfg(SimpleNamespace(notify_config={"slackWebhook": "https://db-webhook"}),
                "slackWebhook", "SLACK_WEBHOOK") == "https://db-webhook"
    assert _cfg(SimpleNamespace(notify_config={}), "slackWebhook", "SLACK_WEBHOOK") == "https://env-webhook"
