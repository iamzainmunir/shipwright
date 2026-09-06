# Demo walkthrough

A 5–7 minute script for showing Shipwright live (or narrating the landing-page tour). Each beat lists
**what to show**, **what to say**, and **why it matters**. Screens referenced live in
[`docs/images/`](images) and auto-play on the landing page (`/`, the *See it in action* section).

> Setup once before you present: orchestrator on `SHIPWRIGHT_ENGINE=graph` + `SHIPWRIGHT_STORE=postgres`,
> web on Node ≥22, at least one model connected under **Models** (a Claude Code CLI seat is the
> quickest — no API key). See the [README](../README.md) quick start.

---

## 1. The pitch (30s) — landing page `/`

- **Show:** the hero and the auto-playing product tour.
- **Say:** "Shipwright is an autonomous AI software company. You file a ticket; a team of agents — PM,
  architect, engineers, QA, reviewer, DevOps — takes it through spec, build, code review, QA, and
  ship, and hands you a running app. You hold the gates that matter."
- **Why:** frames it as a *company*, not a chatbot — a real lifecycle with humans on the gates.

## 2. Connect a model (45s) — **Models**

- **Show:** add a connection → **Claude Code CLI** → default `auto`, effort, autonomy = Safe.
- **Say:** "Bind any model per agent — Anthropic, OpenAI-compatible, local Ollama, or your Claude
  Code CLI seat. Put your strongest model on the CTO and QA agents — the verdicts are only as good as
  the judge behind them."
- **Why:** shows bring-your-own-model + that reviewer/QA quality is a first-class concern.

## 3. Dispatch a mission (45s) — **New mission**

- **Show:** "Build a simple notes app with an API and a UI." Set autonomy to *supervised*. Add a
  `parallel` label to fan out backend + frontend.
- **Say:** "The PM frames it, drafts a spec with machine-checkable acceptance criteria, and plans it
  into a dependency-ordered set of tickets — one per part, assigned by role."
- **Why:** the work is decomposed and assigned like a real team, not one monolithic prompt.

## 4. Watch it build (2–3 min) — **Live Build**

- **Show:** the pipeline advancing: `intake → clarify → spec → plan → build → review → QA → ship`.
  Point at the parallel engineers working in their own worktrees, then the merge.
- **Say:** "Code review runs **before** QA — the CTO reviews correctness, security, and scope first,
  then QA verifies the reviewed build by actually running it. So what ships is exactly what QA
  blessed. If QA or review flags a fault, only the failing part rebuilds — not the whole app."
- **Why:** this is the core differentiator — a decision graph with targeted rework, not a one-shot.

## 5. QA that runs the real app (45s) — **Live Build → QA / Tickets → evidence**

- **Show:** the QA evidence — screenshots, the served URL, criterion-by-criterion checks.
- **Say:** "QA boots the app's own server and drives a headless browser. The verdict is grounded in
  what actually ran — a deterministic gate can only downgrade a shaky 'looks good', never ship a
  stub."
- **Why:** proves "reality over claims" — the anti-hallucination guarantee.

## 6. The board moved itself (30s) — **Tickets**

- **Show:** the board: To Do → In Progress → In Review → QA → Done, tickets now in **Done**.
- **Say:** "The team drove this board itself. Every card carries its owner, its live status, and the
  QA evidence behind the verdict. There's an optional one-way Jira mirror too."
- **Why:** familiar, auditable project tracking — no separate bookkeeping.

## 7. Run the app (30s) — **Live Build → Run app**

- **Show:** click **Run app** → it boots on a free localhost port → open the link → real UI + API.
- **Say:** "One click boots the shipped app on a free port and hands you a link — the real thing, not
  a snapshot. Stop frees the port."
- **Why:** the deliverable is runnable code, demonstrated end to end in the room.

## 8. The gate is yours (20s) — ship

- **Show:** the ship/merge gate pausing for authorization when pushing to a real repo.
- **Say:** "Even fully autonomous, Shipwright can't force-push or auto-approve a risky merge. You always
  decide what reaches the outside world."
- **Why:** trust — autonomy with a human-owned boundary.

---

### One-liners for Q&A

- **"Is it deterministic?"** The engine is a checkpointed LangGraph; a deterministic assessor, not an
  LLM, has the final say on whether a build is real and QA actually passed.
- **"What if agents disagree?"** A builder that hits ambiguity emits a `CTO-QUESTION:` and stops; the
  CTO gives one decisive answer and it's re-run — parallel agents stay coherent.
- **"Does it loop forever?"** No. Every backward edge is budgeted; when budgets are spent the run
  halts and asks you.
- **"Can I use it without an API key?"** Yes — a local Ollama runtime, or your Claude Code CLI seat.

> **On a recorded video:** the in-product tour on `/` cycles the real screens automatically and is the
> intended "demo reel." To produce an actual screen-recording, run the walkthrough above with any
> capture tool (macOS `⇧⌘5`, QuickTime, or Loom) — the script's timings are sized for a ~6-minute clip.
