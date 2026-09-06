# How Shipwright works

A guided tour of what happens between filing a ticket and getting a running app — the pipeline, the
team, the models, and the controls you hold. This is the conceptual companion to the [README](../README.md).

---

## 1. The mental model

Shipwright is a **software company staffed by AI agents**. A *mission* is a unit of work (a feature, a
fix, a whole app). A *team* of role-locked agents takes the mission through a lifecycle and produces
real code on disk. You are the stakeholder: you set the brief, choose how much autonomy to grant,
and own the gates that touch the outside world (merges, deploys).

Two principles run through everything:

- **Reality over claims.** A phase's "it's fine" is never trusted on its own. The build's real files,
  a headless-browser run of the app, and deterministic checks decide — a hallucinated pass can't ship.
- **Stay in lane.** Every agent does *only* its role's job and escalates rather than guessing.

---

## 2. The pipeline (a decision graph)

A run is not a straight line. Each phase emits a **verdict** that routes the work forward, back to an
earlier phase, or to the CTO for a direction call. The canonical forward order is:

```
intake → clarify → spec → plan → build → review → QA → ship
```

| Phase | Who | What happens | Verdict → next |
|---|---|---|---|
| **intake** | PM | Read the ticket, frame the problem & scope | → clarify |
| **clarify** | PM | Ask blocking questions (or note none) | → spec |
| **spec** | PM | Draft the spec + **machine-checkable acceptance criteria** | → plan |
| **plan** | PM + CTO | Break into a dependency-ordered **task graph**; create one ticket per part, assigned by role + skill | → build |
| **build** | Engineers | Implement the parts (parallel, in git worktrees), merge into one codebase | → review |
| **review** | CTO | **Code review**: correctness, security, scope, tech bar | APPROVE → QA · REWORK → build (targeted) · ESCALATE → CTO decision |
| **QA** | QA | **Behaviour verification**: run the real app, check each acceptance criterion | PASS/PARTIAL → ship · REWORK → build (targeted) |
| **ship** | DevOps | Merge/deploy behind your approval gate | done |
| *CTO decision* | CTO | Only when review/QA escalates | REDESIGN → spec · REBUILD → build · PROCEED → QA/ship |

### Why review before QA

Code review is cheap and catches code-level problems early; QA is the *final* gate that proves the
reviewed build actually works. Running review first means you never spend a QA cycle on code that
review is about to change, and **what ships is exactly what QA verified**. (A pure git-op mission
skips QA — there's no build to exercise.)

### Rework is *targeted*

When review or QA sends work back, Shipwright rebuilds **only the part that failed**, off the current
codebase — a frontend note goes to the frontend engineer only; the backend isn't touched. The failing
QA/review feedback is handed to that engineer as explicit fix instructions ("QA reported X — fix
exactly this"). A CTO *redesign/rebuild* is a deliberate fresh direction and rebuilds the whole app.

### Loops are always finite

Every backward edge has its own capped budget (QA reworks, review reworks, CTO consultations). A
"no-progress" guard also trips if a rebuild changes nothing yet QA still fails. When budgets are
spent, the run **halts and asks you** — it never loops forever and never force-ships.

### The never-ship-broken gate

Independently of any LLM verdict, a deterministic assessor checks the build did real work (files
written, not a scaffold) and QA's machine evidence. It can only *downgrade* a shaky pass to rework —
so a confident-but-wrong "PASS" can't ship a stub.

---

## 3. The team & roles

A mission runs on a **team** — a set of agents, one accountable per role. Roles are lane-locked (the
system prompt + file-ownership enforce it):

- **PM** — the *what & why*: scope, spec, acceptance criteria, the plan.
- **CTO** — architecture + the **code review** (the final code-quality gate) + direction calls when things escalate.
- **Backend / Frontend** — build their slice only (APIs/data vs UI/client); a backend agent won't style UI.
- **QA** — verifies *behaviour* against acceptance criteria, using the harness evidence. It does **not** review code style, architecture, or security — that's the reviewer's job.
- **DevOps** — merge, deploy, rollback.
- **Designer / BA / Security** — UX, requirements analysis, threat modelling.

**Escalation, not guessing.** If a builder hits a genuine ambiguity or a conflict with existing code,
it emits a `CTO-QUESTION:` and stops; the CTO gives one decisive answer and the builder is re-run with
it. This keeps parallel agents coherent instead of each inventing its own approach.

Configure teams and per-agent model bindings under **Team** and **Models**. The team hierarchy view
lays roles out by the agentic SDLC: Leadership → Product & Design → Engineering → Quality & Delivery.

---

## 4. Models — bring your own

Every agent can run on a different model; you connect models under **Models** and bind them per agent
(with a failover chain).

- **Cloud APIs** — Anthropic, and any OpenAI-compatible provider (OpenAI, Gemini, Groq, Mistral, DeepSeek, Together, Cohere, OpenRouter, …). One key each.
- **Local** — Ollama (free, runs on your hardware).
- **Claude Code CLI** — your local `claude` binary, driven as a subprocess (the ToS-sanctioned way to use a Claude subscription seat programmatically — the binary holds the credential; the token is never lifted). Pick a **default model** (`auto`/`opus`/`sonnet`/`haiku`), an **effort** level, and a **build autonomy** (Safe = write files only · Full = run commands in the isolated worktree). Reasoning phases run `claude -p`; the build runs the CLI as a coding agent inside each worktree, streaming its file/command activity to the console. If your seat is signed out, the connection test says so — run `claude auth login`.

**A capable judge matters.** QA/review verdicts are only as good as the model behind them — put the
CTO/QA agents on a strong model (e.g. Opus via the CLI) so verdicts are reliable, not the bottleneck.

Usage is metered per run (real tokens + cost), and you can set per-model budgets and rate caps.

---

## 5. Parallel builds

An app build is a **fork-join**:

1. The plan is a set of disjoint-file, role-tagged subtasks with a dependency DAG.
2. Subtasks run **wave by wave** — each wave's agents work in their own git worktree in parallel; the wave is merged into `main` before the next starts, so later agents build on real upstream code, not guesses.
3. A README is always guaranteed; the whole tree is reported so QA/review judge the complete app.

Greenfield apps build solo by default (one coherent author); add a `parallel` label (or ask for it in
the brief) to fan out across backend + frontend engineers.

---

## 6. QA that runs the real app

The QA evidence harness (`app/qa_harness/`) is deterministic and LLM-free — it gathers *facts*:

1. **Detect** the project type (static / Vite-React / Node-served) — including a bare `server.js` with no `package.json`.
2. **Serve** it — boots the app's *own* server (`npm start` / `node server.js`), or builds+serves a Vite bundle, or static-serves; on a free port.
3. **Smoke-check** — page loads (<400), non-empty body, each acceptance criterion's expected text is present.
4. **Capture** — headless Chromium confirms the page renders, records real console errors (benign resource 404s like a missing favicon are ignored), and saves screenshots.

It degrades gracefully (browser missing → HTTP checks; can't serve → static) and **never fails a run
by itself** — its evidence *grounds* the QA verdict. The QA agent then grades each criterion by
observable behaviour, treating a passing harness check as runtime proof.

---

## 7. The ticket board

A built-in Jira-like board tracks every mission live:

```
To Do → In Progress → In Review → QA → Done        (+ Blocked for paused work)
```

Stories are created at *plan* time (one per subtask, assigned to the named engineer) and move with the
pipeline: build → **In Review**, review-approved → **QA**, QA-passed → stays in QA (ready to ship),
shipped → **Done**. QA failures file bugs owned by the QA engineer and blocking the story; a *partial*
pass ships with a tracked follow-up. The board polls live, so columns update without a refresh. An
optional, one-way **Jira mirror** (off by default) reflects the board into Jira — Jira is always an
observer, never a participant in pipeline state.

---

## 8. Run app

On a shipped (or built) mission, **Details → Run app** boots the built app on a free localhost port
and gives you a link to open and test it — real API and UI, not a static snapshot. Click **Stop** to
free the port. (It reuses the QA harness's launcher, so it runs the app's own server.)

---

## 9. Autonomy — when you're in the loop

Set per mission:

| Level | You are asked… |
|---|---|
| **manual** | at every step |
| **assisted** | for the meaningful decisions |
| **supervised** | at the gates that matter (spec, ship) |
| **autonomous** | only when something needs a human — the pipeline otherwise runs unattended |

Even **autonomous** can't force-push or auto-approve a risky merge: pushing to a real repo where your
branch conflicts pauses at the **merge gate** for your explicit authorization. You always decide what
reaches the outside world.

---

## 10. Persistence & engine

- **Store** — `SHIPWRIGHT_STORE=postgres` persists missions, runs, tickets, agents, and metering (RLS-scoped per workspace). `memory` is ephemeral (wiped every restart).
- **Engine** — `SHIPWRIGHT_ENGINE=graph` runs the checkpointed LangGraph `StateGraph`; `legacy` the hand-rolled loop. Both implement the same decision graph and gates.

See the [README](../README.md) for setup and the exact env switches.
