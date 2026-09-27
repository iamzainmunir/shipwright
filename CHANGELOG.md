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
- **Learning is now visible (P7, backend).** The engine emits compact learning events so a UI (and the
  run event stream) can show what knowledge is actually in play, live: `skill.recalled` (with each skill's
  effectiveness + uses), `memory.recalled`, `recall.empty` (so silent non-use is visible — surfaces A8),
  `lesson.written` (from a rework), and `skill.learned` (a newly distilled skill). `GET /skills` now
  returns effectiveness (`successes`/`fails`/`uses`); the web panels consume all of this.
- **WhatsApp conversational control plane (P4).** You can now drive missions from WhatsApp free text —
  **start** a mission, **cancel/end** it, **answer the AI's clarifying questions**, **approve/reject**
  gates, and query **status** — with a per-sender **session state machine** (`wa_sessions`, TTL-bounded,
  RLS):
  - destructive actions (start, cancel) require an explicit **confirm** step; nothing fires on the first
    message.
  - the AI's clarifying questions are **pushed to WhatsApp** with a reply protocol and **arm the session**,
    so the next plain reply routes to `submit_clarification` for the right mission.
  - sender identity is normalized (digits-only), so Twilio `whatsapp:+1…` and Meta `1…` map to the same
    session; all inbound stays **signature-verified + sender-allow-listed** and every action is attributed
    to `whatsapp:<number>`; unknown senders are ignored.
  - a new `engine.start_mission_from_text(brief)` turns a chat brief into a mission + run.
- **Spec Contract + strong API QA (P1).** A mission now has ONE documented, versioned **Spec Contract**
  (`contracts` table) that binds spec → build → QA → review to the same expectations — no more "Dev built
  X, QA expected Y" drift:
  - the spec phase emits two machine blocks (`foundry-criteria` UI items + a new `foundry-api` block);
    `contract.parse_contract` folds both into one contract, created once and injected into build (against
    it), QA and review (grade against it).
  - a deterministic **API-checks rung** in the QA harness issues **real requests** against the served app
    and asserts **status, content-type, response shape (required keys), and error hygiene** (unknown route
    → 404, malformed body → 4xx-not-500, JSON error not a leaked stack trace), plus endpoint **discovery**
    (OpenAPI, or FastAPI/Express/Next source scan). Safe verbs only — it never fires an undeclared mutation.
  - a failing **blocking** API item becomes a graded criterion, so it **reopens the build** (a broken API
    can no longer pass on an LLM verdict); advisory checks stay warnings.
  - a structured **feedback ledger** (`contract_feedback`) records expected-vs-actual per contract item on
    every QA failure and is fed back into the next **rework** build, so fixes target the documented gap.
  - `GET /missions/{key}/contract` and `/contract/feedback` expose it.
- **Researcher agent (P6).** A new **`researcher`** role that can search and read the internet
  (**read-only**) and produce **cited research briefs** to ground spec/build/QA in current facts. It is
  hardened because it is the one role touching untrusted web content:
  - **SSRF guard** — private/loopback/metadata/link-local addresses and non-http(s) schemes are hard
    blocked; optional per-workspace allowlist + denylist (`is_blocked_url`).
  - **Secret scrubber** — a query/URL that looks like it carries a token/credential is refused, so
    secrets never leave in a web request.
  - **Prompt-injection boundary** — fetched pages are framed as **untrusted DATA, never instructions**;
    an injection attempt in a page is **flagged, not obeyed**; research can only ever produce a
    brief/memory — never a write, push, spend, or message-send.
  - **Cited-only output** — a claim with no resolvable source URL is dropped (no uncited "facts");
    briefs persist per mission (`research_briefs`, RLS) and become reusable **`REFERENCE`** memories.
  - **Offline-first (Rule 0)** — with no `SHIPWRIGHT_RESEARCH_PROVIDER` configured, research is
    unavailable and the opt-in pre-spec `research` phase no-ops; nothing breaks. Network tools are
    role-gated (only `researcher`) and can never run in the build/QA sandbox loop.

### Fixed
- **Auto-grown skills never persisted.** `_persist_skill` referenced a nonexistent `SkillSource.PROJECT`
  and raised inside a swallow-all `except`, so the skill library never actually grew. Fixed with a real
  `SkillSource.LEARNED`. (root cause behind "skills aren't really learned")
- **A red test suite could ship (A15).** `_assess_build` couldn't tell a green build from an untested
  one. A `tests_ran` signal now threads agent → `BuildResult` → build-facts, and `_assess_build` marks a
  build **unhealthy when a suite actually ran and failed** (a known-red build no longer ships); "no tests
  run" stays advisory, so an untested build is never false-failed.
- **An unverified build could auto-ship (A2, CRITICAL).** With the sandbox off (the default), a build
  produced no ground-truth facts, so the health check fell back to *healthy* and an **autonomous** mission
  auto-approved the merge on the LLM's QA verdict alone. A per-mission **`verified`** signal is now set
  **only** by genuine evidence (a real build assessed against files/steps/tests, a deliverable
  reconstructed from disk, or runtime QA evidence — screenshots/smoke/API checks; a fully-degraded harness
  counts as *unverified*, not healthy). The autonomous merge auto-approves **only when verified** —
  otherwise it halts at the human gate and never ships on a verdict alone. Verification is re-earned every
  build attempt (cleared at `build.api` and on discard), so a stale flag can't carry a new change through.
  Holds for both the legacy and graph engines (both ship only through the same gate).
- **CI `alembic check` gate.** Reconciled the ORM with the migration-built schema (unique
  constraints/indexes on `tickets`/`jira_issue_map`/`jira_outbox`/`custom_roles`/`projects`, and the
  `autonomy_policies` notify columns kept `NOT NULL` to match a fresh `alembic upgrade head`).
- **CI never ran on `master`.** The `push` trigger targeted `main`, but the repo's default branch is
  `master` — so direct pushes to master produced **zero** check-runs and every fix looked "unverified".
  The trigger now covers `[master, main]`, so pushes to master are actually gated.
- **RLS gate hardcoded a stale table count.** The isolation gate asserted exactly `10` FORCE-RLS tables /
  `ws_isolation` policies; the schema has since grown to 19 tenant tables, so the gate failed even though
  every table is correctly protected. Rewritten to be **self-maintaining** — it derives the tenant-table
  set from the `workspace_id` column and asserts *each* one has FORCE RLS + a `ws_isolation` policy (with
  a floor so it can't pass vacuously). A new tenant table that forgets RLS now trips the gate by name.

### Changed
- **Skills now earn their place (A14).** Each skill tracks `successes`/`fails` — runs that recalled it and
  then shipped vs reworked/halted. Recall **ranks by effectiveness** (ship-rate, tie-broken by uses) and
  injects only the top few role-relevant skills; a skill with enough evidence and a poor ship-rate is
  **auto-demoted** (`auto_invoke=False`) so the library stops re-injecting what doesn't help. Metrics were
  previously write-only. (migration 0019)
- **Pipeline safety-stop is derived from the loop caps** (`MAX_CTO_DECISIONS`, `MAX_REWORK_CYCLES`, …)
  instead of a magic `40`, so it is always an outer net and cannot drift from the caps. (audit A4)
- **Memory recall no longer silently returns nothing.** When the offline lexical embedder finds no
  overlap (common across different missions), recall falls back to the most **recent** memories so
  lessons still surface. (audit A8)
- **Recalled skills are role-relevant and actionable.** `_recall_skills` filters to the phase's role
  (no arbitrary `skills[:2]` fallback) and surfaces each skill's **instructions/procedure**, not just a
  one-line description, so a recalled skill can actually change how the agent works. (audit A5/A6)

### Planned (tracked, next)
- P7 learning transparency UI · P5 WhatsApp Agent Platform long-poll channel (transport).

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
