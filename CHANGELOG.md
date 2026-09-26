# Changelog

All notable changes to Shipwright are documented here. This project follows
[Keep a Changelog](https://keepachangelog.com/) and Semantic Versioning.

## [Unreleased]

An adversarial audit (2026-09-26, 23-agent verification workflow) surfaced 15 confirmed issues across
verification-trust, runaway loops/cost, and the skills/memory learning loop. This work lands
**P0 (loop & cost guardrails)**, the **P2 compounding-learning loop**, and the retrieval fixes that
make it fire (**P3, part 1**) — all additive/low-risk. The verification-gate semantics change
(A2/A15) and the larger feature phases follow.

### Added
- **Per-run cost ceiling.** `SHIPWRIGHT_RUN_COST_BUDGET_CENTS` (0 = uncapped) is now enforced: a run
  that reaches the budget **halts for the user** instead of spending or shipping past it — in both the
  legacy and graph engines. (audit A3)
- **Compounding-learning loop.** The richest signals no longer evaporate at run end:
  - a **rework cause** (QA/review/CTO sending work back) is persisted immediately as an embedded
    **`LESSON`** memory, deduped, so the next similar mission recalls it instead of re-hitting it (A1);
  - a **failed run** now **reflects** into a `FAILURE` memory — failures teach, not just ships (A11);
  - auto-grown skills are **grounded in the real deliverable + the rework the run went through**, and
    carry a populated **category / trigger / instructions** (a usable procedure), not a bare slogan
    (A12/A13).

### Fixed
- **Auto-grown skills never persisted.** `_persist_skill` referenced a nonexistent `SkillSource.PROJECT`
  and raised inside a swallow-all `except`, so the skill library never actually grew. Fixed with a real
  `SkillSource.LEARNED`. (root cause behind "skills aren't really learned")
- **A red test suite could ship (A15).** `_assess_build` couldn't tell a green build from an untested
  one. A `tests_ran` signal now threads agent → `BuildResult` → build-facts, and `_assess_build` marks a
  build **unhealthy when a suite actually ran and failed** (a known-red build no longer ships); "no tests
  run" stays advisory, so an untested build is never false-failed.
- **CI `alembic check` gate.** Reconciled the ORM with the migration-built schema (unique
  constraints/indexes on `tickets`/`jira_issue_map`/`jira_outbox`/`custom_roles`/`projects`, and the
  `autonomy_policies` notify columns kept `NOT NULL` to match a fresh `alembic upgrade head`).
- **CI never ran on `master`.** The `push` trigger targeted `main`, but the repo's default branch is
  `master` — so direct pushes to master produced **zero** check-runs and every fix looked "unverified".
  The trigger now covers `[master, main]`, so pushes to master are actually gated.

### Changed
- **Pipeline safety-stop is derived from the loop caps** (`MAX_CTO_DECISIONS`, `MAX_REWORK_CYCLES`, …)
  instead of a magic `40`, so it is always an outer net and cannot drift from the caps. (audit A4)
- **Memory recall no longer silently returns nothing.** When the offline lexical embedder finds no
  overlap (common across different missions), recall falls back to the most **recent** memories so
  lessons still surface. (audit A8)
- **Recalled skills are role-relevant and actionable.** `_recall_skills` filters to the phase's role
  (no arbitrary `skills[:2]` fallback) and surfaces each skill's **instructions/procedure**, not just a
  one-line description, so a recalled skill can actually change how the agent works. (audit A5/A6)

### Planned (tracked, next PRs)
- **A2 — fail-closed ground-truth gate** + **A15 — red test suite blocks ship** (change routing
  semantics; need test-suite updates).
- **A14** skill/agent effectiveness metrics driving routing (needs a migration).
- P1 Spec Contract + API QA · P7 learning UI · P4/P5 WhatsApp control plane · P6 Researcher agent.

## [2026-09] — on `master`

### Added
- **Two-way notifications & control** over **Slack** (Socket Mode — no public URL), **WhatsApp**
  (Twilio + Meta Cloud API), and **Email** (IMAP polling): opt-in alerts when a mission is blocked,
  needs approval, ships, or halts; reply to **approve/reject** a gate or ask for **status** from chat.
  Credentials are DB-backed and redacted; every inbound request is signature-verified (Slack/WhatsApp)
  or gated by a sender allow-list + DMARC check (Email).
- **Coordinated multi-project change** — select several repos and prompt once; a new API is wired into
  the gateway in the same run, under one review → QA → ship gate.
- Notification setup UI with per-channel credential forms, "Send test" with per-channel results, a
  folder path picker, and configurable mission-key prefix + projects directory.
