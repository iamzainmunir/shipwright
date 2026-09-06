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

- **Status queries** (read-only): `status`, `missions`, `mission <KEY>`, `tickets <KEY>`, `models`, `help`.
- **Approvals**: resolve the mission's open approval gate.
  - **Slack** — the approval alert carries **Approve / Reject** buttons.
  - **WhatsApp** — reply `approve <KEY>` or `reject <KEY>` (e.g. `approve SW-142`).

Every inbound request is **signature-verified** against your stored credentials before Shipwright
acts — an unsigned or tampered request is rejected with `401`.

> Inbound webhooks need a **public URL**. In local dev, expose the orchestrator (`:8000`) with a
> tunnel (`cloudflared tunnel --url http://localhost:8000`, `ngrok http 8000`, …) and use that host
> in the webhook URLs below.

### Slack

1. Create a Slack app with an **Incoming Webhook**; paste the webhook URL in Settings.
2. For two-way, also paste the app's **Signing secret**, then set the request URLs:
   - **Slash Commands** → `https://<host>/api/v1/integrations/slack/commands`
   - **Interactivity** → `https://<host>/api/v1/integrations/slack/interactivity`

### WhatsApp · Twilio

1. Enable the WhatsApp sandbox (or a real sender) and paste Account SID / Auth token / sender.
2. Under **Messaging → "A message comes in"**, set the inbound webhook to
   `https://<host>/api/v1/integrations/whatsapp/twilio` (verified with your Auth token; replies go
   back inline via TwiML).

### WhatsApp · Meta (Cloud API)

1. Paste the **Access token** + **Phone-number ID**.
2. For two-way, add a **Verify token** (any string you choose) and the app's **App secret**.
3. In the app's **WhatsApp → Configuration** webhook, use
   `https://<host>/api/v1/integrations/whatsapp/meta` and the same Verify token. Meta calls it once
   with a `GET` challenge (echoed back when the token matches); inbound messages arrive as `POST`
   requests signed with the App secret, and replies go out via the Cloud API.
