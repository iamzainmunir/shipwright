"""Shared test fixtures.

Tests must be hermetic — independent of the developer's local ``.env`` (which sets
``FOUNDRY_STORE=postgres``, ``GITHUB_REPO=…`` etc. for the running app). Without this, a configured
``GITHUB_REPO`` with no token would make the merge gate re-open ("connect GitHub first") instead of
performing the local dry-run these tests expect.
"""

from __future__ import annotations

import pytest
from app.config import get_settings


@pytest.fixture(autouse=True)
def _hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force GitHub push config empty (env vars beat the .env file) and use the in-memory store, so
    the push path is a local dry-run unless a test explicitly wires a connector."""
    monkeypatch.setenv("GITHUB_REPO", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("GITHUB_BASE", "main")
    monkeypatch.setenv("FOUNDRY_STORE", "memory")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
