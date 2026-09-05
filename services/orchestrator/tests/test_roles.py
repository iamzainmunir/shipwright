"""Per-role catalog: locked default skills + role-scoped system prompts."""

from __future__ import annotations

from app.api.v1.runs import _with_role_skills
from app.roles import ROLE_CATALOG, role_skills, role_system_prompt


def test_role_catalog_covers_the_pipeline_roles() -> None:
    for role in ("frontend", "backend", "qa", "cto", "pm", "devops"):
        assert role in ROLE_CATALOG
        assert role_skills(role)  # non-empty


async def test_locked_role_skills_always_present_and_first() -> None:
    merged = await _with_role_skills("frontend", ["my-extra", "react"])
    # Every locked frontend skill is present…
    for s in role_skills("frontend"):
        assert s in merged
    # …role skills come first, the custom extra follows, and the duplicate "react" isn't repeated.
    assert merged[: len(role_skills("frontend"))] == role_skills("frontend")
    assert merged.count("react") == 1
    assert "my-extra" in merged


async def test_locked_skills_cannot_be_removed() -> None:
    # Simulate the user clearing the field: the role's skills are still enforced.
    merged = await _with_role_skills("backend", [])
    assert set(role_skills("backend")).issubset(set(merged))


def test_role_system_prompt_states_the_lane() -> None:
    fe = role_system_prompt("frontend")
    assert "FRONTEND" in fe and "Does NOT" in fe
    be = role_system_prompt("backend")
    assert "Does NOT" in be and "UI" in be
