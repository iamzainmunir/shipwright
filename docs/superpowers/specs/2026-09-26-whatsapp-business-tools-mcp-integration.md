# Spec — WhatsApp Agent Platform Integration (Shipwright as the service behind a WhatsApp Agent)

> **Status:** Design spec — no implementation yet. **Transport/setup** layer for the WhatsApp control
> plane; the *runtime* logic (start/end missions, answer questions, approvals, status) is **P4** in
> `docs/superpowers/plans/2026-09-26-shipwright-self-improvement-qa-whatsapp.md`. P4 stays channel-agnostic;
> this P5 adds the **WhatsApp Agent Platform** as a new, no-tunnel channel.
>
> **Contract source:** *WhatsApp Agent Platform — Developer Manual, Version 1 (published 2026-08-25)*,
> provided by the user. The connection contract below is **verified from that manual**, so the former
> "discovery gate" (P5-T0) is **RESOLVED**.

**Goal:** Let a user create a **"Shipwright" agent inside WhatsApp** (Settings → Agents → Create an agent),
paste the generated **API token** into Shipwright, and run their whole org from a normal WhatsApp chat —
start/end missions, answer the AI's questions, approve/reject gates, get status — with **no Twilio, no
Cloud-API app, and no public URL/tunnel** (the receive model is server-side long-poll).

**Security first:** the token is an **opaque Bearer secret**; it lives in **Settings → Notifications →
WhatsApp Agent** (DB, redacted) — never chat/code/logs/commits. **Rotate the token pasted in conversation
on 2026-09-26.** And note the **privacy caveat**: this channel is **not end-to-end encrypted**.

---

## Verified contract (from the Developer Manual v1)

- **Base URL:** `https://api.whatsapp.com/agent/v1`
- **Auth:** `Authorization: Bearer <ACCESS_TOKEN>`. Opaque string — treat as a secret, do not parse.
  Rotation invalidates it; uninstalling the app forces regeneration. Missing/malformed header → `401`
  (`error.code 190`); present-but-invalid → `400` (`error.code 100`).
- **Setup (user side):** WhatsApp → **Settings → Agents → Create an agent** → set display name + avatar →
  open the agent's chat → **Chat info → API key** → copy.
- **Participant identifiers:** `<type>:<id>` — `user:<id>` (a WhatsApp user) / `agent:<id>` (the agent).
  **Opaque**: compare in full, **never show to a user**. `to` accepts **`user:<id>` only** (else `400`,
  `131009`). Identifiers refer to an account and **can change** → treat as a **conversation key, not a
  user primary key**; always reply to the `from` of a recent inbound message. The agent converses only
  with **its creator**.

### Receive — `GET /agent/v1/updates` (long-poll; NO webhook, NO public URL)
- Query: `offset` (pointer; pass `next_offset` from the previous response; omit ⇒ start at head and skip
  backlog; `offset=0` ⇒ from the beginning), `limit` (default 50, ≤100), `timeout` (default 15s, 0–25).
- Holds the connection open until an update arrives or the timeout elapses (empty response on timeout).
- Response: `{ object:"whatsapp_agent_platform", entry:[{ id, changes:[{ field:"messages",
  value:{ messaging_product, contacts:[{ wa_id:"user:<id>", profile?:{name} }], messages:[…], statuses:[…] }}]}],
  next_offset }`.
- **Message object:** `{ from:"user:<id>", id:"wamid…", timestamp, type, text:{body}?,
  image|audio|video|document|sticker:{id,mime_type,sha256,caption?,…}?, reaction:{message_id,emoji}?,
  context:{id,from}? }`.
- `statuses[]` = delivery/read receipts for messages the agent sent. **Update retention: 30 days.**
- **Offset discipline:** persist `next_offset` and pass it on the next poll ⇒ ordered, at-least-once, no
  missed messages across restarts.

### Send — `POST /agent/v1/messages`
- Body: `{ messaging_product:"whatsapp", to:"user:<id>", type:"text|image|audio|video|document|sticker",
  <type>:{…}, context?:{ message_id:"wamid…" } }`. Text max **4096** chars; caption max **1024**.
- Response: `{ contacts:[{input,wa_id}], messages:[{id}] }` — record `messages[].id`.
- **Retry rules:** `2xx` sent (don't retry); `4xx` fix request/token (don't retry except `429` → back off);
  `503`/`131016` not accepted → retry after backoff; `500`/reset/read-timeout → **unknown**, may double-send
  (decide in advance). **Do not issue concurrent sends to the same recipient** (ordering not guaranteed).
  Set the client read timeout well above normal send time.

### Other endpoints
- **`POST /agent/v1/statuses`** — read receipts + **typing indicator** (nice UX: show "typing…" while the
  org is working on a reply).
- **Media** — `POST /agent/v1/media`, `GET /agent/v1/media/<id>` + download, `DELETE …` (image ≤5MB;
  video/audio/document ≤16MB; sticker ≤500KB). Optional for us (e.g. send a QA screenshot).

### Rate limits
- **12 req/min** for `POST /messages`, `POST /statuses`, and each media method; **15 req/min** for
  `GET /updates`. The channel must self-throttle + back off on `429`.

Sources: WhatsApp Agent Platform Developer Manual v1 (2026-08-25, user-provided). Public background:
[WABetaInfo](https://wabetainfo.com/whatsapp-is-rolling-out-chats-with-third-party-agents-on-ios/).

---

## Why this is the best-fit channel
Pull-based long-poll + a Bearer token means **zero inbound infrastructure** — no Cloud-API app, no phone-
number provisioning, no webhook, no tunnel. It reuses the **exact same inbound brain** we built
(`inbound.resolve_text`) + the P4 session machine, and the transport mirrors the **email IMAP poller**
pattern already in the codebase (`app/email_poller.py`): a background loop that polls, routes, replies,
and persists a cursor. Lowest-friction on-ramp to the whole control plane.

---

## Design

### A new channel behind the existing brain (mirrors `email_poller`)
```
Background task (per configured WhatsApp Agent, in app/main.py lifespan — like _email_poll_loop)
   loop:  GET /agent/v1/updates?offset=<stored next_offset>&timeout=15&limit=50   (long-poll)
     → for each entry.changes[].value.messages[]:
          sender = message.from  (user:<id>)   ── the agent's creator
          text   = message.text.body           (ignore non-text types for v1, ack politely)
          reply  = wa_session/​wa_control over inbound.resolve_text(...)   ← P4 (unchanged)
          POST /agent/v1/messages { to: sender, type:"text", text:{body: reply} }
     → persist next_offset (per agent)          ── so restarts resume, no missed/duplreplayed msgs
   respect rate limits (15/min updates, 12/min sends); back off on 429/503; failure-isolated.
```
P4 owns *what* commands do; P5 owns *how this channel receives, throttles, replies, and tracks offset*.

### Modules
- `app/wa_agent_channel.py` (new): the poll/send client + offset cursor + rate-limit/backoff + retry rules.
- `app/main.py`: `_wa_agent_loop()` background task in the lifespan (alongside `_email_poll_loop`,
  `_slack_socket_loop`), inert until the token is configured (Rule 0), cancelled on shutdown.
- Reuses `inbound.resolve_text`, `wa_session`, `wa_control` (P4); `notifier` for proactive
  question/approval pushes (send a message to the creator when a run needs them).

### Config & state (DB)
- `notify_config.whatsappAgentKey` (**secret**, in `_NOTIFY_SECRET_KEYS`), `whatsappAgentId`/name.
- **Offset cursor** persisted per agent: `notify_config.whatsappAgentOffset` (or a small `WaAgentState`
  row). On cold start with no stored offset, **omit `offset`** (skip 30-day backlog) then persist
  `next_offset`; thereafter always resume from the stored value.
- Entered/managed in **Settings → Notifications → WhatsApp Agent** with the not-E2E disclosure + "Send test".

### Sender trust
The agent talks only to its **creator**, and identifiers are opaque and may change. Trust model:
learn the creator's `user:<id>` from the first inbound and treat that conversation as authorized
(the allow-list is implicitly the single creator); still require **explicit `confirm`** for high-impact
actions (start/cancel) per P4, attributed to `whatsapp-agent:<id>`. Never render the raw identifier to the user.

### Privacy & safety (elevated — no E2E encryption)
Chats route through Meta's relay (not E2E). Since this channel carries mission detail + **approval
decisions**:
- **Sensitivity minimization:** approval/question/status messages carry the minimum (mission key + short
  summary + the ask) — never secrets, tokens, diffs, or full logs. A setting caps detail (default: minimal).
- **Prominent UI disclosure** that this channel is not E2E-encrypted (Slack Socket Mode / email remain for
  a different trust model).
- Token stored redacted; never logged; rotate-on-leak documented; per-action confirm on destructive ops.
- Respect rate limits + WhatsApp agent policy.

### Interfaces (signatures / shapes only)
- `wa_agent_channel.py`: `async poll_updates(cfg, offset) -> Updates`; `async send_text(cfg, to, body, *, context_id=None) -> str`; `async run_forever(store, engine)`; internal rate-limiter + backoff.
- `main.py`: `_wa_agent_loop()` lifespan task.
- `notifier.py`: `push_agent(mission, blocker)` for proactive question/approval delivery + optional typing indicator via `POST /statuses`.
- `notify_config` keys above; store helper for the offset cursor.
- UI: a "WhatsApp Agent" settings card (key, disclosure, detail-level, Send test).

### Tasks (spec-level, each testable)
- [x] **P5-T0 — Contract discovery.** RESOLVED — contract captured above from the Developer Manual v1.
- [ ] **P5-T1 — Config + Settings card.** DB key (redacted) + offset cursor + not-E2E disclosure + Send test. Acceptance: channel inert until the key is set; key redacted on read; disclosure visible.
- [ ] **P5-T2 — Updates poller + offset persistence.** `wa_agent_channel.run_forever` + `_wa_agent_loop`. Acceptance (mock HTTP): long-poll consumes `messages[]`, persists `next_offset`, resumes from stored offset after a restart (no re-processing, no gaps); empty/timeout responses are no-ops; failure-isolated with backoff.
- [ ] **P5-T3 — Send + retry/rate-limit rules.** `send_text` with the manual's retry semantics, self-throttle (12/min), 429/503 backoff, no concurrent sends per recipient. Acceptance: ret/limit rules unit-tested against mocked responses; `to` always `user:<id>`.
- [ ] **P5-T4 — Route inbound → P4 brain.** Wire text messages through `resolve_text`/`wa_session`/`wa_control`; non-text types get a polite "text only for now". Acceptance: a `status` message returns the status reply; `approve M-152` resolves the gate; start/cancel require confirm; creator-only trust enforced.
- [ ] **P5-T5 — Proactive question/approval push + typing.** `notifier.push_agent` sends the ask to the creator when a run blocks; optional typing indicator while composing. Acceptance: a `QUESTION`/`APPROVAL` blocker delivers a minimal message; the reply routes to `submit_clarification`/`resolve_blocker`.
- [ ] **P5-T6 — Sensitivity minimization + docs.** Cap message detail; `docs/notifications.md` gains a "WhatsApp Agent (no-tunnel)" section with the create-in-WhatsApp → paste-key flow + the privacy caveat. tsc clean.

### Risks
- **No E2E encryption** for approval traffic → sensitivity minimization + disclosure + confirm gates.
- **Beta/geo availability** → the feature may not be enabled on the user's account/region yet; other channels remain.
- **Offset loss / double-processing** → persist cursor transactionally; idempotent handling of a replayed message; skip backlog on cold start.
- **Rate limits** → self-throttle + backoff; coalesce rapid replies.

### Open Questions
1. **Backlog on first connect** — skip the 30-day backlog (recommended: omit offset on cold start) or read from `offset=0`?
2. **Detail level** over this non-E2E channel — minimal (key + ask) by default, with an opt-in "full detail"?
3. **Priority** vs the master plan's P0 (stop-unverified-ships) — build this channel now, or after P0?

---

## Appendix A — WhatsApp Business Tools MCP (optional, separate)
Distinct 2026 feature: an **MCP server** connecting AI coding agents (Claude/Cursor/Codex) to the
**WhatsApp Business Platform** to automate *developer setup* (Business account, number verification, Cloud
API registration, template + webhook management). **Not** the consumer Agents feature this spec targets; a
possible future dev-convenience for the separate Twilio/Cloud-API channel. Not on P5's critical path.
Source: [TechCrunch](https://techcrunch.com/2026/09/15/meta-now-lets-ai-agents-handle-the-boring-parts-of-whatsapp-business-setup/).
