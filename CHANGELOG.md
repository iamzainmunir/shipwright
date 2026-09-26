# Changelog

All notable changes to Shipwright are documented here. This project follows
[Keep a Changelog](https://keepachangelog.com/) and Semantic Versioning.

## [Unreleased]

An adversarial audit (2026-09-26, 23-agent verification workflow) surfaced 15 confirmed issues across
verification-trust, runaway loops/cost, and the skills/memory learning loop. The full remediation +
feature plan lives in [`docs/superpowers/plans/2026-09-26-shipwright-self-improvement-qa-whatsapp.md`](docs/superpowers/plans/2026-09-26-shipwright-self-improvement-qa-whatsapp.md).
This release lands **P0 (part 1): loop & cost guardrails** — the additive, low-risk fixes; the
verification-gate hardening follows in the next PR (it changes routing semantics).

### Added
- **Per-run cost ceiling.** `SHIPWRIGHT_RUN_COST_BUDGET_CENTS` (0 = uncapped) is now enforced: a run
  that reaches the budget **halts for the user** instead of spending or shipping past it — in both the
  legacy and graph engines. (audit A3)
- **Planning docs & specs** under `docs/superpowers/` for the multi-phase roadmap: trust & guardrails,
  Spec Contract + strong API QA, compounding learning loop, retrieval & routing, learning-transparency
  UI (P7), the **WhatsApp Agent Platform** channel (contract verified from Meta's Developer Manual v1),
  and a **Researcher agent** role.

### Changed
- **Pipeline safety-stop is now derived from the loop caps** (`MAX_CTO_DECISIONS`, `MAX_REWORK_CYCLES`,
  `MAX_ESCALATIONS`, …) instead of a magic `40`, so it is always an outer safety net (never the
  effective limiter) and cannot drift from the caps it is meant to backstop. (audit A4)

### Planned (tracked, next PRs)
- **A2 — fail-closed ground-truth gate** (`_build_is_healthy` must not pass unverified builds; make real
  build/QA evidence the default) and **A15 — a known-red test suite blocks ship**. Staged separately
  because they change routing semantics and require test-suite updates.
- P1 Spec Contract + API QA · P2 compounding learning · P3 retrieval/routing · P7 learning UI ·
  P4/P5 WhatsApp control plane · P6 Researcher agent (see the plan).

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
