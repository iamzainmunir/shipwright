"""Role catalog — the single source of truth for what each agent role DOES, the standard skills it
carries, and the internal system prompt that keeps it in its lane.

Skills + scopes are drawn from current (2026) industry role definitions. The catalog is used three ways:
  * ``seed_agents`` / ``create_agent`` assign a role's default skills (locked, always present).
  * the reasoning + build phases prepend the role's system prompt so an agent does ONLY its own work
    (a Backend agent won't style UI; a Frontend agent won't design the data model).
  * the API exposes it (``GET /roles``) so the Team UI can show the locked skills.
"""

from __future__ import annotations

# role_key → (default skills, one-line scope of DOES vs DOES NOT)
ROLE_CATALOG: dict[str, dict[str, object]] = {
    "frontend": {
        "skills": ["react", "typescript", "css", "responsive-design", "accessibility",
                   "state-management", "web-performance", "component-testing"],
        "scope": ("Builds and styles the user-facing UI in the browser — components, layout, "
                  "client-side state, and accessibility. Does NOT design server APIs, data models, "
                  "or business logic."),
    },
    "backend": {
        "skills": ["api-design", "databases", "business-logic", "auth", "caching",
                   "integration-testing", "cloud-services", "tdd"],
        "scope": ("Builds server-side APIs, data models, and business logic (persistence, auth, "
                  "integrations). Does NOT do UI markup, styling, or visual design."),
    },
    "devops": {
        "skills": ["ci-cd", "containers", "infrastructure-as-code", "cloud-platforms",
                   "observability", "automation", "deploys", "rollback"],
        "scope": ("Automates build/deploy pipelines and provisions, scales, and monitors "
                  "infrastructure. Does NOT write product features or UI."),
    },
    "qa": {
        "skills": ["test-automation", "e2e-testing", "api-testing", "regression",
                   "performance-testing", "accessibility-testing", "bug-triage", "acceptance-tests"],
        "scope": ("Verifies the built product meets its acceptance criteria by testing BEHAVIOUR "
                  "across UI, API, and data, and gates releases. Does NOT review code style, "
                  "structure, architecture, or security, and does NOT implement features or infra — "
                  "code review is the reviewer/CTO's job; QA judges only whether the running app DOES "
                  "what the criteria require."),
    },
    "cto": {
        "skills": ["architecture", "technology-strategy", "engineering-leadership",
                   "tech-stack-decisions", "security-oversight", "scaling", "code-review"],
        "scope": ("Sets technical vision + architecture standards and reviews/approves the work "
                  "(final quality gate). Does NOT write day-to-day feature code."),
    },
    "ceo": {
        "skills": ["vision-strategy", "decision-making", "stakeholder-communication",
                   "go-to-market", "hiring", "partnerships"],
        "scope": ("Owns company vision and overall direction. Does NOT make hands-on technical or "
                  "design implementation decisions."),
    },
    "pm": {
        "skills": ["product-strategy", "roadmapping", "prioritization", "user-discovery",
                   "requirements-specs", "stakeholder-management"],
        "scope": ("Decides WHAT to build and why — roadmap, priorities, specs. Does NOT design "
                  "visuals or write production code."),
    },
    "ba": {
        "skills": ["requirements-elicitation", "process-modeling", "user-stories",
                   "data-analysis", "documentation", "gap-analysis"],
        "scope": ("Elicits, analyzes, and documents business + functional requirements. Does NOT own "
                  "product vision/prioritization or write code."),
    },
    "designer": {
        "skills": ["ux-design", "ui-visual-design", "prototyping", "design-systems",
                   "wireframing", "user-research", "accessibility"],
        "scope": ("Designs the user experience + interface — research, flows, visuals, prototypes. "
                  "Does NOT implement production code or define backend logic."),
    },
    "security": {
        "skills": ["threat-modeling", "secure-code-review", "vulnerability-management",
                   "cloud-security", "devsecops", "auth", "incident-response"],
        "scope": ("Finds and mitigates security risks across code, infra, and CI/CD. Does NOT own "
                  "feature delivery or general infra ops."),
    },
}


def role_skills(role: str) -> list[str]:
    """The standard, locked skills for a role (empty for unknown/custom roles)."""
    entry = ROLE_CATALOG.get(role)
    return list(entry["skills"]) if entry else []  # type: ignore[index]


def role_scope(role: str) -> str:
    entry = ROLE_CATALOG.get(role)
    return str(entry["scope"]) if entry else ""


def role_system_prompt(role: str) -> str:
    """The internal system prompt that keeps an agent in its lane. The composer now lives in
    :mod:`app.prompts` (the single prompt library); this preserves the public API. Lazy import
    avoids a cycle (``prompts`` imports this module's ``ROLE_CATALOG``/``role_scope``)."""
    from . import prompts
    return prompts.role_system_prompt(role)
