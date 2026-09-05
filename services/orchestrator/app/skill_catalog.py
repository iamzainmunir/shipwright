"""A curated catalog of installable agent skills — the Skills "Marketplace".

These are real, self-contained capability definitions (name, description, trigger, and the
instructions an agent follows when the skill is invoked). Installing one creates a real Skill
record in the workspace via POST /skills. This is a fixed, versioned catalog shipped with the
product — not fabricated usage data. Keep entries practical and provider-agnostic.
"""
from __future__ import annotations

# Each entry mirrors the SkillBody create shape: name, description, category, trigger, instructions.
CATALOG: list[dict[str, str]] = [
    {
        "name": "code-review",
        "description": "Review a diff for correctness, security, and readability before merge.",
        "category": "engineering",
        "trigger": "A pull request or diff is ready for review.",
        "instructions": (
            "Read the full diff. Flag correctness bugs, missing edge cases, security issues, and "
            "unclear names. Prefer concrete failing scenarios over style nits. Approve only when "
            "tests cover the change and no blocking issue remains."
        ),
    },
    {
        "name": "root-cause-analysis",
        "description": "Trace a failure to its true cause instead of patching the symptom.",
        "category": "engineering",
        "trigger": "A bug, incident, or failing test needs diagnosis.",
        "instructions": (
            "Reproduce the failure, form one hypothesis at a time, and confirm it with evidence "
            "before fixing. State the root cause and the minimal fix; add a regression test."
        ),
    },
    {
        "name": "api-contract",
        "description": "Design and document a clean, versioned HTTP/JSON API surface.",
        "category": "engineering",
        "trigger": "A new endpoint or service boundary is being added.",
        "instructions": (
            "Define resources, methods, status codes, and error shapes. Keep names consistent, "
            "paginate lists, version breaking changes, and document each field with an example."
        ),
    },
    {
        "name": "acceptance-tests",
        "description": "Turn a spec's stories into runnable pass/fail acceptance checks.",
        "category": "quality",
        "trigger": "A spec has acceptance criteria that need verification.",
        "instructions": (
            "For each story, write a check with clear given/when/then steps and an observable "
            "outcome. Report pass/fail with evidence; never mark a check passed without proof."
        ),
    },
    {
        "name": "threat-model",
        "description": "Enumerate attack surfaces and mitigations for a change (STRIDE).",
        "category": "security",
        "trigger": "A change touches auth, data access, or external input.",
        "instructions": (
            "Walk STRIDE (spoofing, tampering, repudiation, info disclosure, DoS, elevation). "
            "For each realistic threat, note the mitigation or accept-with-reason. Prioritize by impact."
        ),
    },
    {
        "name": "dependency-audit",
        "description": "Check dependencies for known CVEs and risky version drift.",
        "category": "security",
        "trigger": "Dependencies changed or a periodic audit is due.",
        "instructions": (
            "List direct and transitive dependencies with versions. Flag known vulnerabilities and "
            "unmaintained packages. Recommend the smallest safe upgrade and note breaking changes."
        ),
    },
    {
        "name": "prd-writer",
        "description": "Draft a crisp product requirements doc from a rough idea.",
        "category": "product",
        "trigger": "A new initiative needs a spec before build.",
        "instructions": (
            "Capture the problem, target user, goals, non-goals, and success metrics. Write user "
            "stories with acceptance criteria. Keep it one page; cut anything not decision-relevant."
        ),
    },
    {
        "name": "release-notes",
        "description": "Summarize shipped changes into user-facing release notes.",
        "category": "product",
        "trigger": "A version is ready to ship.",
        "instructions": (
            "Group changes into Added / Changed / Fixed. Write each line for the user's benefit, not "
            "the implementation. Call out breaking changes and required migration steps up front."
        ),
    },
    {
        "name": "ci-pipeline",
        "description": "Set up lint, test, build, and gated deploy stages for CI.",
        "category": "devops",
        "trigger": "A repo needs continuous integration or its pipeline is failing.",
        "instructions": (
            "Define stages: install (cached), lint, test, build, deploy-behind-approval. Fail fast, "
            "surface logs, and make the pipeline reproducible locally. Keep secrets out of logs."
        ),
    },
    {
        "name": "incident-runbook",
        "description": "Produce a step-by-step runbook to detect, mitigate, and recover.",
        "category": "devops",
        "trigger": "A service needs an on-call runbook for a known failure mode.",
        "instructions": (
            "Document detection signals, first-response mitigation, rollback steps, and verification. "
            "Include who to page and how to confirm recovery. Keep steps copy-pasteable."
        ),
    },
    {
        "name": "accessibility-check",
        "description": "Audit a UI for WCAG contrast, keyboard, and semantics issues.",
        "category": "design",
        "trigger": "A screen or component is ready for review.",
        "instructions": (
            "Check color contrast, focus order, keyboard operability, labels/roles, and reduced-motion. "
            "Report each failure with the element and the WCAG criterion; suggest the fix."
        ),
    },
    {
        "name": "empty-states",
        "description": "Design honest, helpful empty and error states for a feature.",
        "category": "design",
        "trigger": "A view can render with no data or after a failure.",
        "instructions": (
            "For every list/detail view, define the zero, loading, and error states. Say what happened "
            "and the next action. Never show fabricated placeholder data in place of a real empty state."
        ),
    },
]
