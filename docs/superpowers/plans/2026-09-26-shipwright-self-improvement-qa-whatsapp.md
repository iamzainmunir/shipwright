# Shipwright — Trustworthy Verification, Compounding Learning & WhatsApp Control Plane

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This document is **specs only — no implementation code yet** (per the author's instruction); each task carries interfaces, data shapes, and acceptance criteria, not function bodies.

**Goal:** Close the audit's critical gaps so Shipwright (a) never ships unverified work, (b) verifies the **API/response layer** against a single documented contract, (c) actually **learns and compounds** across missions, and (d) can be driven **end-to-end from WhatsApp** (start/end missions, answer the AI's questions, approve gates, query status).

**Architecture:** Keep the existing seams — `RunEngine` decision-graph pipeline (`spec → build ↔ QA → review → CTO → ship`), the `qa_harness` evidence package, the DB-backed store (`InMemoryStore` + `PostgresStore` behind the `Store` protocol), and the notifier/inbound two-way layer. All new behavior slots behind these seams: a **Spec Contract** as the single source of truth, an **API-checks** rung in the QA harness, a **reflection/lesson** learning loop keyed off the pipeline's existing rework signals, a **retrieval** upgrade (real embeddings + build-agent injection), and a **conversational WhatsApp session** state machine on top of the existing inbound endpoints.

**Tech stack:** Python 3.13 / FastAPI / SQLAlchemy 2 + Alembic / Pydantic v2 / `httpx` / Playwright (existing QA capture) / `slack-sdk` (Socket Mode) / Twilio + Meta WhatsApp Cloud API. Web: Next.js 15 / React 19 / TypeScript.

**Spec:** this document is self-contained; it is grounded in the audit report of 2026-09-26 (23-agent adversarial workflow, 15 confirmed findings) and the code as read at that date.

---

## Global Constraints

Copied verbatim from the platform's standing rules — every task's requirements implicitly include these:

- **Rule 0 — offline-first / drop-in real backend.** Every new subsystem is inert until configured; a missing dependency degrades, never crashes a run.
- **Observer rule — failure isolation.** Notifications, WhatsApp I/O, reflection, and evidence gathering MUST NEVER raise into or fail a run.
- **Lifecycle invariant — never ship unverified/broken work.** Verdicts fail safe; reviews are grounded; the ground-truth gate is authoritative; halt rather than force-ship when budgets exhaust.
- **Persistence.** Orchestrator runs with `FOUNDRY_STORE=postgres`; new tables use Alembic migrations chained after `0015`; every tenant table gets RLS `ENABLE + FORCE` + a `ws_isolation` policy and relies on the `app.workspace_id` GUC.
- **Secrets.** All credentials/config live in the DB (`AutonomyPolicy.notify_config`) or connectors, redacted on read, merged on write; never `.env` for user-facing secrets. Env vars are ops fallback only via `_cfg`.
- **GitHub push consent.** Never force-push or switch branches autonomously; the user decides at the ship gate.
- **Both engines.** `GraphEngine` subclasses `RunEngine` and reuses `_next_phase`; every engine-level change MUST hold for both.
- **Clean code / SOLID.** New logic lives in focused modules with clear interfaces; match surrounding style; tests accompany each unit.
- **Copy.** User-facing product name is "Shipwright". Mission keys use the configurable workspace prefix (default `M-`, e.g. `M-151`).

---

## Audit Baseline (what we are fixing)

15 confirmed findings (severity after adversarial verification). IDs referenced by tasks as `A#`.

| A# | Sev | Theme | One-line |
| --- | --- | --- | --- |
| A2 | CRITICAL | Trust | Ground-truth gate `_build_is_healthy` (engine.py:2400) fails **open**; sandbox off by default → ships on LLM verdict alone |
| A15 | MED | Trust | Red/never-run tests don't block ship (`_assess_build`, engine.py:284) |
| A3 | HIGH | Loops/cost | Per-run cost/token budget defined, **never enforced** (engine.py:1938) |
| A4 | HIGH | Loops/cost | Runaway backstop is a magic phase-count (40), inconsistent with real caps (engine.py:115) |
| A1 | CRITICAL | Learning | Rework feedback (`_rework_reason`) discarded at run end (engine.py:2061); never persisted |
| A11 | HIGH | Learning | Learning is ship-only — failed/halted/force-shipped runs teach nothing (engine.py:853) |
| A10 | HIGH | Learning | Memory written only on ship; content near-informationless (engine.py:1708) |
| A12 | HIGH | Learning | Auto-skills are slogans from ticket text; `instructions`/`trigger` empty (engine.py:1740) |
| A7 | HIGH | Learning | No quality gate; exact-name dedup only → near-dup skills auto-inject forever (engine.py:1762) |
| A13 | MED | Learning | Skills hardcoded `category=ENGINEERING`, `instructions=None` (engine.py:1769) |
| A14 | MED | Learning | Metrics write-only: `uses`/`agent.stats` never affect routing; wrong skills never retired (engine.py:1913) |
| A8 | HIGH | Retrieval | "Semantic" recall is a lexical hash stub; zero-overlap dropped → memory rarely fires (embedding.py:112) |
| A6 | HIGH | Retrieval | Skills dumped by role category, never ranked by relevance; arbitrary `skills[:2]` fallback (engine.py:1789) |
| A5 | HIGH | Retrieval | Build agents get no skills; only name+desc surfaced, never `instructions` (engine.py:1792) |
| A9 | HIGH | Retrieval | Memory/skills injected only into reasoning phases, never into code-writing agents (engine.py:1886) |

**The pattern:** learning is *ship-only, shallow, and never retrieved where it would change code*; and verification has a default-open hole. The five phases below fix this and layer the two requested features (strong API QA + WhatsApp control) onto the same seams.

---

## File Structure (what each new/changed unit owns)

**New files**
- `services/orchestrator/app/qa_harness/api_checks.py` — deterministic API request/response assertions.
- `services/orchestrator/app/qa_harness/discover.py` — endpoint discovery (OpenAPI + repo scan).
- `services/orchestrator/app/contract.py` — Spec Contract parse/serialize/validate + feedback ledger helpers.
- `services/orchestrator/app/reflection.py` — `_reflect` / `_remember_lesson` distillation (LLM-assisted, failure-isolated).
- `services/orchestrator/app/wa_session.py` — WhatsApp conversation state machine + intent routing.
- `services/orchestrator/app/wa_control.py` — maps parsed WhatsApp intents onto engine/API actions.
- `apps/web/app/dashboard/missions/[key]/contract/` — Contract & feedback-ledger UI panel.
- Alembic: `0016_contract.py`, `0017_lesson_memory_skill_fields.py`, `0018_wa_sessions.py`.

**Changed files**
- `app/engine.py` — gate fails closed; budget enforcement; reflection hooks; retrieval into build; skill/memory persistence rework.
- `app/qa_harness/{harness,detect,checks}.py` — add API rung to the ladder + evidence.
- `app/specdoc.py` + `libs/py-core/src/foundry_core/models.py` — Contract model + richer `AcceptanceCriterion`.
- `app/memory_search.py` + `libs/py-core/src/foundry_core/embedding.py` — pluggable embedder + recency fallback.
- `app/prompts.py` — contract-aware spec/build/QA prompts; recalled skills' `instructions` surfaced.
- `app/notifier.py` + `app/inbound.py` + `app/whatsapp_integration.py` + `app/api/v1/whatsapp.py` — WhatsApp control plane.
- `app/config.py` — new settings (embedder, budgets, sandbox default, WhatsApp session TTL).
- `apps/web/lib/foundry.ts` + settings/mission pages — contract panel, WhatsApp config, budget display.

---

## Phase P0 — Trust & Guardrails (ship nothing unverified; bound spend)

**Rationale:** A2/A15 make the product's core promise false by default; A3/A4 leave spend unbounded. Smallest, highest-urgency phase; everything else builds on a gate that actually holds.

### Design

- **Verified flag.** Introduce a per-mission/run `verified` signal that is **only** set true when a ground-truth check actually executed (`_assess_build` ran a real build/test, or the `qa_harness` produced runtime evidence against on-disk artifacts). Absence ⇒ unverified.
- **Fail closed.** `_build_is_healthy` returns `(False, "build was never verified")` when there are **no** build facts **and** no on-disk deliverable for a code/docs mission — routing then halts-for-user instead of auto-shipping. A degraded harness (`crit_total == 0`, no runtime checks) is treated as *unverified*, not *healthy*.
- **Real evidence by default.** Either default `sandbox_enabled=True`, or when off, force `build.api` to write an on-disk deliverable the `qa_harness` can serve, so evidence is real in the default config.
- **Red tests block.** `BuildResult` carries `tests_ran: bool` and `tests_passed: bool` (or exit code + passed/total). `_assess_build` returns unhealthy on `tests_ran and not tests_passed`; the test result is surfaced into `qa_evidence["checks"]` as a **blocking** machine check so `_classify_qa` reopens on a red suite. "No tests run" stays advisory but never counts as verified-green.
- **Budget enforcement.** After each `update_run` usage accumulation (engine.py:1934-1941, :2251-2258) and once per main-loop iteration, if `settings.run_cost_budget_cents > 0` and `run.cost_cents >= budget` (and/or a token cap), stop the run via `_halt_for_user` / a `BlockerKind.BUDGET` blocker — **never ship on budget exhaustion.** Same check wired into `graph_engine` node.
- **Safety-stop derived from caps.** Replace the magic `40` with `_PIPELINE_SAFETY_STOP` derived from the real caps (e.g. `(MAX_CTO_DECISIONS + 1) * worst_case_transitions_per_segment`); keep the graph `recursion_limit` derived from the same expression so they cannot drift.

### Interfaces (signatures only)
- `Mission`/run gains `verified: bool` (or engine-tracked `self._verified: dict[str,bool]`), set by `_assess_build` / `_run_qa_evidence`.
- `BuildResult.tests_ran: bool`, `BuildResult.tests_passed: bool` (devloop).
- `RunEngine._build_is_healthy(mission_id) -> tuple[bool, str]` — new fail-closed semantics.
- `RunEngine._enforce_budget(run) -> bool` — returns True if the run must halt.
- `config.Settings`: `sandbox_enabled` default review; `run_cost_budget_cents`, `run_token_budget` honored.

### Tasks
- [ ] **P0-T1 — Verified flag + fail-closed gate.** Files: `app/engine.py`. Interface: `_build_is_healthy` new semantics + a `verified` tracker set only by real evidence. Acceptance: with sandbox off and no on-disk artifact, an LLM `PASS` at QA routes to **halt-for-user**, not ship (unit test drives `_classify_qa`/`_decide` with empty evidence and asserts non-ship).
- [ ] **P0-T2 — Real evidence in default config.** Files: `app/engine.py`, `app/config.py`. Acceptance: a default-config code mission produces on-disk artifacts the `qa_harness` serves (or sandbox runs), so `_build_facts` is populated before QA; test asserts facts non-empty on the default path.
- [ ] **P0-T3 — Red tests block ship.** Files: `app/devloop.py` (`BuildResult` fields + populate from test invocation), `app/engine.py` (`_assess_build`), `app/qa_harness/checks.py` (surface as blocking check). Acceptance: a build whose test command exits non-zero yields `healthy=False` and `_classify_qa` reopens; a green suite passes; "no tests" stays advisory.
- [ ] **P0-T4 — Budget enforcement.** Files: `app/engine.py`, `app/graph_engine.py`. Interface: `_enforce_budget`. Acceptance: a run whose `cost_cents` crosses the configured budget halts with a `BUDGET` blocker and never reaches ship (unit test with a stubbed cost).
- [ ] **P0-T5 — Cap-derived safety stop.** Files: `app/engine.py`. Acceptance: `_PIPELINE_SAFETY_STOP` computed from caps; graph `recursion_limit` from the same expression; test asserts they stay consistent when a cap changes.

### Risks
- Defaulting sandbox on changes runtime cost/latency — gate behind config with a clear default and doc.
- Fail-closed may surface more halts-for-user; acceptable (correct) but note in release notes; the WhatsApp control plane (P4) makes resolving halts fast.

---

## Phase P1 — Spec Contract (single source of truth) + Strong API QA

**Rationale:** Eliminates the "Dev built X, QA expected Y" drift by binding spec/dev/QA/review/CTO to one documented, versioned contract, and adds deterministic **API/response** verification — the requested "carefully check all APIs, responses."

### Design

**1. The Contract, one artifact.** Promote the fragmented `foundry-criteria` block + `Subtask.produces` prose into a structured, versioned `Contract` persisted on the mission and rendered as a readable doc. It has two item families, each with a stable id + `severity` (`blocking`/`non_blocking`):

- **UI items** (existing shape retained): `{id, criterion, route, expect_text[], expect_selector[]}`.
- **API items** (new): `{id, criterion, method, path, request:{schema?, headers?, auth?, example?}, response:{status, contentType?, requiredKeys[]?, schema?, example?}, errors:[{when, status, shape?}]}`.

**2. Authoring.** The spec/PM agent emits the contract at spec time (extend `CRITERIA_ASK` in `specdoc.py` to include the API block; `extract_criteria` → `extract_contract`, fail-soft). The CTO can amend it on a redesign. It is **versioned** (each amendment bumps `contract.version`).

**3. Distribution.** Inject the **relevant slice** of the contract into every agent prompt: builders get the exact endpoints/UI they own (replacing vague `produces`), QA/review get the full contract to grade against. One source of truth ⇒ no divergent expectations.

**4. Deterministic API verification.** New QA-harness rung:
- `discover.py`: enumerate endpoints — fetch OpenAPI/Swagger if served (`/openapi.json`, `/swagger.json`, `/api-docs`); else scan built repo for route definitions (FastAPI `@router.*`, Express `app.<verb>`, Next.js `app/api/**/route.ts`). Union with contract API items.
- `api_checks.py`: issue real requests against the served app and assert per endpoint — **status** (2xx valid; **404** unknown route; **4xx not 500** on malformed body), **content-type**, **response shape** (required keys / JSON-schema), **error hygiene** (JSON error, not a stack trace). Each assertion → a `CheckResult`.
- `harness.py`: add API checks to the degradation ladder as first-class `QaEvidence`; **blocking** API failures feed the QA verdict (grounding) → build reopens. Reuses the `serve()` context and the fail-safe contract (never raises into a run).

**5. Structured feedback ledger.** Every QA finding / review comment / CTO note references a contract item id and carries `{contract_id, expected, actual, severity, feedback}`. A blocking failure auto-opens a **Bug ticket** on the board linked to the contract item. The ledger is visible to all agents (recalled into rework prompts) and the user (UI panel). This replaces prose-only "make it better."

### Interfaces (signatures / shapes only)
- `models.py`: `class ContractItem(FoundryModel)` (union of UI/API fields above), `class Contract(FoundryModel){ id, mission_id, workspace_id, version, items: list[ContractItem], created_at, updated_at }`. `AcceptanceCriterion` folded into `ContractItem` (or kept as the UI subtype).
- `models.py`: `class ContractFeedback(FoundryModel){ id, contract_id, item_id, run_id, phase, expected, actual, severity, feedback, created_at }`.
- `contract.py`: `parse_contract(spec_text) -> Contract`, `contract_slice(contract, subtask) -> Contract`, `render_contract_md(contract) -> str`, `record_feedback(...) -> ContractFeedback`.
- `qa_harness/discover.py`: `discover_endpoints(project_path, base_url) -> list[Endpoint]`.
- `qa_harness/api_checks.py`: `async api_checks(base_url, contract, endpoints) -> list[CheckResult]`.
- Store: `create_contract/get_contract/upsert_contract/list_contract_feedback/add_contract_feedback` on both stores.
- API: `GET /missions/{key}/contract`, `PATCH /missions/{key}/contract`, `GET /missions/{key}/contract/feedback`.

### Tasks
- [ ] **P1-T1 — Contract model + migration.** Files: `models.py`, `db_models.py`, `0016_contract.py`. Acceptance: contract + feedback persist with RLS; round-trip test on both stores.
- [ ] **P1-T2 — Authoring + extraction.** Files: `specdoc.py`→`contract.py`, `prompts.py`. Acceptance: a spec reply containing the API block parses into `Contract` items; malformed input → empty, never error (fail-soft test).
- [ ] **P1-T3 — Distribution into prompts.** Files: `engine.py`, `devloop.py`, `prompts.py`. Acceptance: a builder prompt for a task owning `GET /api/tasks` contains that exact contract slice; QA prompt contains the full contract.
- [ ] **P1-T4 — Endpoint discovery.** Files: `qa_harness/discover.py`. Acceptance: given a served OpenAPI doc → endpoints enumerated; given a repo with FastAPI/Express/Next routes and no OpenAPI → endpoints enumerated from source (unit tests per framework fixture).
- [ ] **P1-T5 — API checks.** Files: `qa_harness/api_checks.py`. Acceptance: against a fixture app, asserts pass on a conformant endpoint and fail on wrong status / missing key / 500-on-bad-input / HTML error; never raises.
- [ ] **P1-T6 — Ladder + verdict wiring.** Files: `qa_harness/harness.py`, `engine.py`. Acceptance: a blocking API failure appears in `QaEvidence['checks']` and reopens the build via `_classify_qa`; degraded/no-API missions unaffected.
- [ ] **P1-T7 — Feedback ledger + Bug tickets.** Files: `contract.py`, `engine.py`, `tickets.py`. Acceptance: a blocking contract failure records `ContractFeedback` and opens a linked Bug ticket; the rework prompt includes the ledger entries for the failing items.
- [ ] **P1-T8 — Contract UI panel.** Files: `apps/web/.../contract/`, `lib/foundry.ts`. Acceptance: mission page shows the contract (UI + API items) and the feedback ledger; user can view (edit optional/stretch); tsc clean.

### Risks
- Discovery false positives (dead routes) → treat discovered-but-not-in-contract endpoints as **advisory** (warn), contract items as **blocking**.
- Destructive endpoints (POST/DELETE) during checks → default to safe verbs + contract-declared requests only; never fire un-declared mutating calls against a real backend.

---

## Phase P2 — Compounding Learning Loop (self-improvement)

**Rationale:** A1/A10/A11/A12/A7/A13 — capture the richest signals (failures, reworks, decisions) as durable, well-formed lessons so the org stops rediscovering the same mistakes. This is the "self-improvement like a harness" ask.

### Design

- **Lesson on every rework edge (A1).** In `_next_phase`, immediately after `self._rework_reason[...]` is set (QA L1206, review L1243, CTO L1286), call `_remember_lesson(mission, phase_key, cause, failing_items)` → writes an embedded `Memory` (new `MemoryKind.LESSON`) titled `Rework in {phase}: {short cause}`, body = failing contract items + the fix instruction + target files. Dedup on `(phase, normalized-cause)` — repeated identical reworks increment a count on one row, not spam.
- **Reflection on every terminal outcome (A11).** Wrap all terminal branches (`_fail`, halt handler, ship, force-ship) with `_reflect(run_id, mission_id, outcome)`: summarize the run from `_build_facts` + accumulated rework reasons + final verdict; ask the provider for 1–2 durable lessons ("what to do differently"); write them as embedded memories, and — if a reusable corrective procedure emerges — a `Skill` with a **populated `instructions` + `trigger` + real `category`**. Best-effort, failure-isolated.
- **Ground skill/memory in what happened (A10/A12/A13).** `_persist_skill`/`_persist_memory` receive the cached diff/file-list/test-result (`_deliverable_context` already provides this) + rework reasons, and the distill prompt returns `NAME / TRIGGER / CATEGORY / INSTRUCTIONS`. Store all four on `Skill`; enrich ship-memory with key decisions + gotchas, not just path+requirements.
- **Quality gate + semantic dedup (A7).** Before persisting a skill/memory: require a non-trivial body; reject near-duplicates by cosine similarity (via the P3 embedder) against existing rows; on a near-dup, update/reinforce the existing row instead of adding one. Persist learnings on notable **failures** too (repeated QA-fail / CTO-reject), not only ship.

### Interfaces
- `enums.py`: `MemoryKind` gains `LESSON`, `FAILURE` (or reuse `MemoryType`).
- `models.py`: `Skill` gains `trigger: str`, ensures `instructions: str`, real `category: SkillCategory`, and `successes:int`/`fails:int` (used in P3).
- `reflection.py`: `async remember_lesson(store, provider, mission, phase, cause, items) -> Memory | None`; `async reflect(store, provider, run, mission, outcome, facts, rework_reasons) -> None`.
- `engine.py`: call sites at the three rework edges + all terminal branches; `_persist_skill`/`_persist_memory` signatures extended with deliverable + rework context.

### Tasks
- [ ] **P2-T1 — Lesson memory model + migration.** Files: `enums.py`, `models.py`, `db_models.py`, `0017_*`. Acceptance: `LESSON` memories persist + embed on both stores.
- [ ] **P2-T2 — `remember_lesson` at rework edges.** Files: `reflection.py`, `engine.py`. Acceptance: a QA rework writes exactly one embedded LESSON memory; a second identical rework increments its count, not a new row (unit test with a fake store).
- [ ] **P2-T3 — `reflect` on terminal outcomes.** Files: `reflection.py`, `engine.py`. Acceptance: ship, fail, and halt each produce ≥1 lesson memory; a reusable procedure yields a skill with non-empty `instructions`+`trigger`+category; all failure-isolated (a provider error never fails the run).
- [ ] **P2-T4 — Grounded, well-formed distillation.** Files: `engine.py`, `prompts.py`, `reflection.py`. Acceptance: distilled skill carries NAME/TRIGGER/CATEGORY/INSTRUCTIONS from the real diff+reworks (assert fields populated, category not hardcoded).
- [ ] **P2-T5 — Quality gate + semantic dedup.** Files: `reflection.py`, `engine.py`. Acceptance: a vague/duplicate skill is rejected or merged (cosine test); low-value entries never persist.

### Risks
- LLM distillation cost per terminal outcome — cap tokens; run once per outcome; skip when `provider` unavailable.
- Memory growth — dedup + P3 pruning keep recall precise.

---

## Phase P3 — Retrieval & Routing That Actually Fire

**Rationale:** A8/A6/A5/A9/A14 — lessons are worthless if they never reach the agent writing code. Make retrieval real and inject it where behavior changes; let metrics steer.

### Design

- **Real, pluggable embedder (A8).** `get_embedder()` reads `SHIPWRIGHT_EMBED` (`openai|voyage|local|lexical`); constructs a real semantic embedder when configured, keeping `LexicalEmbedder` as the offline fallback. `rank_memories`: when all scores are ~0, fall back to **recency** (return most-recent k) rather than `[]`; emit an observable log when recall is empty.
- **Rank skills by relevance (A6/A5).** `_recall_skills` ranks by embedding similarity over `name + description + instructions`, filtered by role category, top-k; drop the arbitrary `skills[:2]` fallback (return empty rather than inject noise). Surface the `instructions` body (bounded), not just the one-liner.
- **Inject into build agents (A9/A5).** Thread recalled memory + role-relevant skills (with instructions) into `_real_build`/`devloop.build_*` → `prompts.build_task/subtask_task` as an "Apply this team memory / these team skills" block; record skill `uses` for build agents too.
- **Metrics drive routing + retirement (A14).** `Skill` `successes/fails` updated from the ship-vs-rework/halt outcome of runs that recalled it; `_recall_skills` ranks by effectiveness `successes/(successes+fails)` (tie-broken by `uses`); auto-demote (`auto_invoke=False`) below a threshold. `_assign`/provider routing reads `agent.stats` (role success rate). Reflection can **supersede** a memory contradicted by a new outcome (high similarity + opposite conclusion → write correction, mark old superseded).

### Interfaces
- `embedding.py`: `get_embedder() -> Embedder` (config-driven); `class Embedder(Protocol){ embed(text)->list[float] }`.
- `memory_search.py`: `rank_memories(memories, query, k)` with recency fallback; `rank_skills(skills, query, role, k)`.
- `engine.py`: `_recall_skills(mission, role) -> tuple[str, list[Skill]]` (relevance-ranked, instructions surfaced); build path recall injection; `_record_skill_outcome(skill_ids, outcome)`.

### Tasks
- [ ] **P3-T1 — Pluggable embedder + recency fallback.** Files: `embedding.py`, `memory_search.py`, `config.py`. Acceptance: with `SHIPWRIGHT_EMBED=lexical`, zero-overlap query returns recent-k (not empty); a real provider path is constructed when configured (mocked); empty recall logs.
- [ ] **P3-T2 — Relevance-ranked skills + instructions.** Files: `engine.py`, `memory_search.py`, `prompts.py`. Acceptance: `_recall_skills` returns top-k by similarity with `instructions` in the block; no arbitrary fallback (test asserts empty when nothing relevant).
- [ ] **P3-T3 — Inject into build agents.** Files: `engine.py`, `devloop.py`, `prompts.py`. Acceptance: a backend build prompt contains recalled memory + role skills; `uses` incremented for build recalls.
- [ ] **P3-T4 — Effectiveness metrics + routing + retirement.** Files: `models.py`, `engine.py`, `0017_*`. Acceptance: a skill recalled on a run that then reworks/halts gains a `fail`; effectiveness ranks recall; sub-threshold skills auto-demote; routing prefers higher-success agents (unit tests with fabricated stats).

### Risks
- Real-embedder network dependency — always degrade to lexical; never block a run on the embedder.

---

## Phase P4 — WhatsApp Conversational Control Plane

**Rationale:** Requested — run *everything* from WhatsApp: **start** a mission, **end/cancel** it, **answer the AI's clarifying questions**, **approve/reject** gates, and **query status**. Builds on the existing two-way inbound layer (signature verify + sender allow-list + `resolve_text`) and the confirmed engine/API actions.

### Grounding — actions already exist
- Start: `POST /missions` (create) → `POST /missions/{key}/run` (202).
- End: `POST /missions/{key}/cancel` → `engine.cancel_run(mission)` (progress preserved).
- Retry: `POST /missions/{key}/retry`.
- Answer AI questions: `engine.submit_clarification(blocker_id, answers)` — resolves a `BlockerKind.QUESTION` blocker (created by `_ask_clarification`, which suspends the run on a future).
- Approve/reject gate: `engine.resolve_blocker(blocker_id, ApprovalDecision, actor)` (already wired for Slack/WhatsApp).
- Status: existing `resolve_command` (status/missions/mission/tickets/models/help).

### Design

**Conversational, not one-shot.** WhatsApp messages are free text and stateful, so add a per-sender **session** that tracks the current intent and any pending prompt. Inbound flow (in `api/v1/whatsapp.py`, after signature verify + `number_allowed`):

1. Load/create `WaSession` for the sender number.
2. Parse intent (`wa_session.parse_intent(text, session)`): `start | cancel | retry | answer | approve | reject | status | missions | mission | help | confirm | abort`.
3. Route to `wa_control` which calls the engine/store action, mutates session state, and returns the reply text.
4. Persist session; send reply (Twilio TwiML / Meta send API — existing paths).

**Flows (state machine):**
- **Start** — `start build a todo app` → bot confirms/asks for missing bits (title, projects, autonomy) → on `confirm`, create + run mission → reply `Started M-152 ✅ — I'll message you when I need approvals or have questions.` Session holds a `pending_mission_draft`.
- **AI asks a question** — when `_ask_clarification` fires, the notifier pushes the question(s) to WhatsApp with a reply protocol (`Reply with your answer, or "1) ... 2) ..." for multiple`). The inbound `answer` intent maps the reply to the mission's open `QUESTION` blocker → `submit_clarification`. Session tracks `awaiting_answer: {mission_key, blocker_id, questions[]}`.
- **Approvals** — existing approval alert + `approve M-152` / `reject M-152` (already built); session disambiguates when only one gate is open ("approve"/"yes" alone works).
- **End** — `cancel M-152` / `stop M-152` → `cancel_run`; confirm destructive action first (`Reply "confirm" to stop M-152`).
- **Status** — reuse `resolve_command`.

**Notifications become interactive.** Extend `notifier.on_blocker` so a `QUESTION` blocker (not just `APPROVAL`) is pushed to WhatsApp with the reply protocol, gated by `notify_events`. All outbound WhatsApp keeps the existing DB-config + failure-isolation.

**Safety.** All inbound stays behind provider signature verification + the sender **allow-list** (a signed webhook proves the channel, the allow-list proves the person). Start/cancel are **high-impact** → require an explicit `confirm` step, attributed to `whatsapp:<number>`. Session TTL bounds state; unknown senders are ignored (never acted on, never mark their mail/messages).

### Interfaces
- `models.py`: `class WaSession(FoundryModel){ id, workspace_id, sender, state: str, context: dict, updated_at, expires_at }` (state: `idle|awaiting_confirm|awaiting_answer|drafting_mission`).
- `wa_session.py`: `parse_intent(text, session) -> Intent{kind, args}`; `advance(session, intent) -> (reply:str, action:Action|None)`.
- `wa_control.py`: `async execute(action, store, engine, actor) -> str` (create+run / cancel / retry / submit_clarification / resolve_blocker / status).
- `notifier.py`: `on_blocker` pushes `QUESTION` blockers to WhatsApp with reply protocol; new `push_question(mission, blocker)`.
- Store: `get_wa_session/upsert_wa_session/expire_wa_sessions`.
- Config: `notify_config` gains WhatsApp session controls; `notify_events` gains `question`.

### Tasks
- [ ] **P4-T1 — Session model + migration + store.** Files: `models.py`, `db_models.py`, `0018_wa_sessions.py`, `store.py`, `pgstore.py`. Acceptance: sessions persist per sender with TTL + RLS; expiry test.
- [ ] **P4-T2 — Intent parser + state machine.** Files: `wa_session.py`. Acceptance: unit tests map representative messages to intents across states (start/answer/approve/cancel/confirm/status); ambiguous "approve" resolves to the single open gate.
- [ ] **P4-T3 — Control executor.** Files: `wa_control.py`. Acceptance (fake engine/store): `start`→create+run; `cancel`+`confirm`→`cancel_run`; `answer`→`submit_clarification` on the right blocker; `approve`→`resolve_blocker`; each returns a clear reply; unknown/half-finished flows are safe.
- [ ] **P4-T4 — Inbound wiring + confirm gating.** Files: `api/v1/whatsapp.py`, `inbound.py`. Acceptance: signature + allow-list still enforced; start/cancel require `confirm`; end-to-end simulated inbound (Twilio form + Meta JSON) drives a mission start then a cancel.
- [ ] **P4-T5 — Push AI questions + approvals to WhatsApp.** Files: `notifier.py`. Acceptance: a `QUESTION` blocker sends a WhatsApp message with the reply protocol (when the channel + `question` event are on); reply routes to `submit_clarification`; failure-isolated.
- [ ] **P4-T6 — Settings UI + docs.** Files: `apps/web/.../settings`, `docs/notifications.md`. Acceptance: `question` event toggle + allow-list documented; two-way WhatsApp control described end-to-end; tsc clean.

### Risks
- Free-text intent ambiguity → confirm steps for destructive actions; `help` always available; fall back to a usage hint rather than guessing.
- Multi-user / concurrent gates → session context keys pending items by mission key; when >1 open, require the key.
- WhatsApp inbound needs a public URL (tunnel/host) — documented; Slack Socket Mode + email IMAP remain the no-tunnel channels.

---

## Phase P7 — Learning Transparency UI (see skills & memory in use, live)

**Rationale:** The original worry was "no skills/memory being used." P2/P3 make the learning *real*; P7
makes it *visible* — on the live run screen and in dedicated views — so it is obvious what knowledge is in
play and whether it is compounding (or silently doing nothing). This also exposes the A8 "recall returns
empty" case instead of hiding it.

### Grounding (current code)
Skills + Memory pages already exist (`apps/web/app/dashboard/skills/page.tsx`,
`app/dashboard/memory/page.tsx`); the live run console is `apps/web/components/live-build.tsx`; there is a
run/workspace event stream (`GET /runs/{id}/events`, `GET /events/recent`, `listRunEvents`/`recentEvents`
in `lib/foundry.ts`). Today `_recall_skills`/`_recall_memory` inject silently (A5/A9) — nothing is surfaced.

### Design
- **Emit learning events** from the engine at the recall/apply/write points (new event kinds on the
  existing bus): `skill.recalled` (which skills fed a phase + relevance score), `skill.applied` (a role used
  a skill; `uses` incremented), `memory.recalled` (which memories + scores), `lesson.written` (P2 lesson
  from a rework/reflection), `skill.learned` (P2 distilled skill). Empty recall emits `recall.empty` so
  silent non-use is visible.
- **Live run screen** (`live-build.tsx`): render these inline in the console ("🧠 Backend applied skill:
  contract-first-api", "📚 Recalled memory: APIs here use camelCase"), plus a compact **per-run "Learning"
  panel** — Skills fed (with relevance), Memories fed, Lessons produced this run.
- **Per-mission Learning tab:** the skills/memories that fed each run and the lessons it produced (traceable).
- **Skills page enhancements:** per-skill **usage + effectiveness** (`uses`, `successes/fails`,
  effectiveness score from A14/P3), source (project-learned vs installed vs auto-grown), `auto_invoke`
  toggle, "used in N runs / last used", and curation (retire/demote) — turning A14's write-only metrics
  into a visible, actionable surface.
- **Memory page enhancements:** memories by **kind** (lesson/failure/project), source, recall count, and an
  **embedder-status indicator** (real semantic vs offline lexical — surfaces A8 so the user can see whether
  semantic recall is actually on) + search.
- **Command Center tile:** a "Learning" tile — skills learned this week, lessons written, recall hit-rate —
  the at-a-glance answer to "is it actually compounding?".

### Interfaces
- `enums.py`/event payloads: new event kinds above, emitted by `engine.py` at `_recall_skills`,
  `_recall_memory`, `_persist_skill`, `remember_lesson`, `reflect`.
- `lib/foundry.ts`: extend `EventItem`, `Skill`, `MemoryItem` with the new fields (effectiveness, uses,
  kind, recall stats); optional `GET /missions/{key}/learning` aggregation.
- Web: `live-build.tsx` inline + Learning panel; `skills/page.tsx` + `memory/page.tsx` enhancements;
  Command Center tile.

### Tasks
- [ ] **P7-T1 — Emit learning events.** Files: `engine.py`, `enums.py`. Acceptance: a run emits
  `skill.recalled`/`memory.recalled` (or `recall.empty`) at each phase and `skill.applied` on use; visible
  in `GET /runs/{id}/events`.
- [ ] **P7-T2 — Live console + per-run Learning panel.** Files: `live-build.tsx`, `lib/foundry.ts`.
  Acceptance: during a live mission the recalled/applied skills + memories appear inline and in the panel;
  tsc clean.
- [ ] **P7-T3 — Skills page: usage + effectiveness + curation.** Files: `skills/page.tsx`, `lib/foundry.ts`,
  `api/v1/runs.py` (expose fields). Acceptance: each skill shows uses/effectiveness/source and can be
  retired/demoted; changes persist.
- [ ] **P7-T4 — Memory page: kinds + recall stats + embedder status.** Files: `memory/page.tsx`,
  `lib/foundry.ts`. Acceptance: memories grouped by kind with recall counts; a badge shows whether the
  active embedder is semantic or lexical (A8).
- [ ] **P7-T5 — Command Center Learning tile.** Files: dashboard page + metrics. Acceptance: the tile shows
  skills-learned / lessons-written / recall-hit-rate for the workspace.

### Dependencies
P7 needs data from **P2** (lessons/skills written) and **P3** (effectiveness metrics, recall events) to be
meaningful; P7-T1 (event emission) can be built alongside P3. Ship P7 after (or interleaved with) P2/P3.

### Risks
- Event volume / console noise → group learning events into a collapsible panel; inline only the notable ones.

---

## Cross-Cutting

**Data model additions:** `Contract`, `ContractItem`, `ContractFeedback`, `WaSession`; `Skill` (+`trigger`,`instructions`,`successes`,`fails`, real `category`); `Memory`/`MemoryKind` (+`LESSON`,`FAILURE`); `BuildResult` (+`tests_ran`,`tests_passed`); run/mission `verified`.

**Migrations:** `0016_contract`, `0017_lesson_memory_skill_fields`, `0018_wa_sessions` — each with RLS `ENABLE+FORCE` + `ws_isolation` on new tenant tables, chained after `0015`.

**Config/env:** `SHIPWRIGHT_EMBED`, `SHIPWRIGHT_SANDBOX` default, `run_cost_budget_cents`/`run_token_budget`, WhatsApp session TTL, `notify_events.question`.

**Observability:** structured logs when recall is empty, budget halts, gate fails closed, API checks fail, reflection persists a lesson; counters for skills recalled/applied and lessons written.

**Security/privacy:** contract/feedback and WhatsApp sessions are tenant-scoped (RLS); inbound stays signature-verified + allow-listed; destructive WhatsApp actions confirmed and attributed; no secrets in logs; discovered mutating endpoints never fired unless contract-declared.

## Sequencing & Dependencies

```
P0 (trust + budgets) ─► P1 (contract + API QA) ─► P2 (learning loop) ─► P3 (retrieval/routing) ─► P7 (learning UI)
        ├───────────────────────────────────────► P4 (WhatsApp control-plane logic) ─► P5 (WhatsApp Agent channel)
        └───────────────────────────────────────► P6 (Researcher agent)
```
- **P0 first** (correctness gate everything else assumes).
- **P1 before P2** (the contract-feedback ledger is P2's richest learning input).
- **P2 before/with P3** (P2 writes the signal; P3 makes it fire where code is written).
- **P7 after/with P2–P3** (it visualizes what they produce; event emission can be built alongside P3).
- **P4** (control-plane logic) is independent of P1–P3 (depends only on existing engine actions); **P5**
  (the verified WhatsApp Agent long-poll channel) is the transport for P4 and can follow it directly — both
  after P0. P5's contract is verified; no external blocker remains except beta/geo availability.
- **P6** (Researcher) is independent; it strengthens P1 (verify external API contracts from real docs) and
  feeds P2/P3 (cited briefs → memory), so schedule it alongside or after P1.

Separate spec files: **P5** → `docs/superpowers/specs/2026-09-26-whatsapp-business-tools-mcp-integration.md`
(WhatsApp Agent Platform, contract verified); **P6** → `docs/superpowers/specs/2026-09-26-researcher-agent.md`.

## Test Strategy

- Unit tests per task (interfaces above), fail-soft/failure-isolation tests for every new I/O boundary (embedder, API checks, reflection, WhatsApp).
- Engine routing tests driving `_classify_qa`/`_decide`/`_next_phase` with fabricated evidence to prove: fail-closed gate, red-test block, budget halt, lesson-on-rework, reflection-on-terminal.
- Store parity tests (InMemory vs Postgres) for every new model.
- End-to-end simulated WhatsApp inbound (Twilio + Meta) for start → question → answer → approve → cancel.
- Whole suite green + `ruff` + `tsc` clean as the per-phase completion gate (per `verification-before-completion`).

## Open Questions (to confirm before/at each phase)

1. **Sandbox default** — flip `sandbox_enabled` to true globally (simplest correct), or keep off but force on-disk deliverables in default builds? (P0)
2. **Embedder provider** — which real embedder for `SHIPWRIGHT_EMBED` (OpenAI / Voyage / local model)? Affects recall quality + cost. (P3)
3. **Contract editability** — should the user be able to edit the Contract in the UI mid-run, or view-only with CTO-driven amendments? (P1)
4. **WhatsApp start scope** — can a mission be started cold from WhatsApp (with project selection), or only for already-registered projects / a default project? (P4)
5. **API check mutations** — allow contract-declared POST/PUT/DELETE checks against the served app, or restrict automated checks to safe/idempotent verbs by default? (P1)
