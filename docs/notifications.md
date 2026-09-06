# Notifications & two-way control

Shipwright can notify you when a mission is **blocked**, **needs approval**, **ships**, or **halts**,
and — over Slack and WhatsApp — let you **query status** and **approve/reject a gate** without opening
the app. Everything is configured per workspace in **Settings → Notifications**; provider credentials
are stored in the database (redacted on read, never written to `.env`).

Enable a channel, fill in its credentials, and hit **Send test** to confirm delivery.

---

## Outbound alerts

| Channel | What you provide | Sends to |
| --- | --- | --- |
| **Email** | SMTP host / port / user / password (a Gmail App Password, SES, Postmark, …) | Recipient email |
| **WhatsApp · Twilio** | Account SID, Auth token, WhatsApp sender | Recipient number |
| **WhatsApp · Meta** | Access token, Phone-number ID | Recipient number |
| **Slack** | Incoming Webhook URL | The channel the webhook posts to |

Pick which events fire (blocker / approval / shipped / halted) in the same card. A channel whose
credentials or recipient are missing is skipped silently — a notification never fails a run.

---

## Two-way control

Both Slack and WhatsApp accept inbound messages. Two kinds:

- **Status queries** (read-only): `status`, `missions`, `mission <KEY>`, `tickets`, `models`, `help`.
- **Approvals**: resolve the mission's open approval gate.
  - **Slack** — the approval alert carries **Approve / Reject** buttons; `/shipwright status` for queries.
  - **WhatsApp / Email** — reply `approve <KEY>` or `reject <KEY>` (e.g. `approve SW-142`), or `status`.

Each channel has its own trust boundary: **Slack & WhatsApp** are cryptographically signed (an
unsigned/tampered request is rejected); **Email** (unsigned by nature) is gated by a **sender
allow-list** — only replies from addresses you list are ever acted on.

**Which channels need a public URL?**

| Channel | Transport | Public URL / tunnel? |
| --- | --- | --- |
| **Email** | IMAP polling (we reach out to the mailbox) | **No** — works anywhere |
| **Slack** | Socket Mode (outbound WebSocket) | **No** — works behind a firewall |
| **WhatsApp** (Twilio / Meta) | Inbound webhook | **Yes** — a tunnel in local dev |

### Slack (Socket Mode — recommended, no tunnel)

Fastest path: create the app **from the manifest** in [`slack-app-manifest.yaml`](./slack-app-manifest.yaml)
(it pre-sets the `/shipwright` command, Socket Mode, and scopes). Then:

1. **Basic Information → App-Level Tokens** → generate a token with scope `connections:write` → copy the `xapp-…`.
2. **Install App** → copy the Bot User OAuth Token (`xoxb-…`, scope `chat:write`).
3. **Settings → Notifications → Slack**: paste the **Bot token** + **App-level token**, Save. (Keep the Incoming Webhook for the outbound alerts.)
4. `/invite @Shipwright` into your channel, then type `/shipwright status`.

Shipwright opens the WebSocket automatically once both tokens are saved (within ~30s; restart the orchestrator if you change tokens later). The HTTP Request-URL endpoints (`/api/v1/integrations/slack/commands` + `…/interactivity`) still exist if you'd rather run in HTTP mode with a Signing secret + tunnel.

### Email (IMAP polling — no tunnel)

1. Enter your **SMTP** details (used for both the alert and the reply).
2. Add your **IMAP host** (e.g. `imap.gmail.com`). IMAP password defaults to the SMTP password; override it if different.
3. Set **Allowed reply senders** (defaults to the recipient) — only these addresses can approve/query.
4. Reply to any Shipwright alert with `approve SW-142`, `reject SW-142`, or `status`. Shipwright polls the mailbox every ~30s, acts, and replies. It reads only unseen replies to its own alerts and never touches mail from other senders.

### WhatsApp · Twilio (needs a public URL)

1. Enable the WhatsApp sandbox (or a real sender) and paste Account SID / Auth token / sender.
2. Under **Messaging → "A message comes in"**, set the inbound webhook to
   `https://<host>/api/v1/integrations/whatsapp/twilio` (verified with your Auth token; replies go
   back inline via TwiML).

### WhatsApp · Meta / Cloud API (needs a public URL)

1. Paste the **Access token** + **Phone-number ID**.
2. For two-way, add a **Verify token** (any string you choose) and the app's **App secret**.
3. In the app's **WhatsApp → Configuration** webhook, use
   `https://<host>/api/v1/integrations/whatsapp/meta` and the same Verify token. Meta calls it once
   with a `GET` challenge (echoed back when the token matches); inbound messages arrive as `POST`
   requests signed with the App secret, and replies go out via the Cloud API.

> For the WhatsApp channels only, expose the orchestrator (`:8000`) with a tunnel in local dev —
> `cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000` — and use that host above.
> (Note: `:8000` is the orchestrator/API, **not** `:3000` which is the web UI.)
