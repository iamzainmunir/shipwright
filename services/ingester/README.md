# ingester (Go)

Shipwright's inbound **webhook ingester**. It accepts provider webhooks, verifies
each signature, normalizes the delivery, and `XADD`s it to the single Redis Stream
**`ingest:webhooks`** — the hand-off point the orchestrator consumes via consumer
group `g_router` (Canon §13.8). The ingester is inbound-only and stateless with
respect to business logic: it never calls providers back and never touches Postgres.

- **Language / runtime:** Go 1.24 (VERSIONS.md)
- **Port:** `8090` (from `INGESTER_PORT`)
- **Module:** `github.com/iamzainmunir/shipwright/services/ingester`
- **Depends on:** Redis 7 (`github.com/redis/go-redis/v9`)

## Endpoints

| Method | Path                  | Behavior |
|--------|-----------------------|----------|
| `GET`  | `/healthz`            | Liveness. `200 {"status":"ok"}` |
| `POST` | `/webhooks/{provider}`| Ingest a webhook for `{provider}` (`github`, `jira`, `linear`, `gitlab`, `slack`, `sentry`, `vercel`, `figma`) |

`POST /webhooks/{provider}` flow:

1. Read the raw body (bounded to 5 MiB).
2. Verify the provider signature (`verify.go`).
3. Normalize to an event: `{ id, provider, kind, receivedAt, payload }` (JSON, camelCase — Canon §13).
4. `XADD ingest:webhooks` with approximate `MAXLEN ~ 100000` (plan §5.5).

Responses:

| Status | When |
|--------|------|
| `202 Accepted` | verified and enqueued — `{"id","status":"accepted"}` |
| `401 Unauthorized` | signature missing/invalid (fail closed) |
| `404 Not Found` | unknown provider (no verifier registered) |
| `503 Service Unavailable` | Redis unavailable / backpressure — sets `Retry-After: 30` so the provider redelivers |

All error bodies use the Canon §13.5 envelope:
`{ "error": { code, message, details, requestId, retryable } }`.

## Signature verification (`verify.go`)

Ported from plan §5.3. Comparisons are constant-time (`hmac.Equal`), and the
Python `Connector.verify_signature` mirrors this logic against shared fixtures so
the two implementations cannot drift (plan §5.2).

All providers are implemented; each fails closed (`401`) on a missing/invalid signature,
secret, or stale timestamp (`verify.go`, covered by `verify_test.go`):

- **`github`** — `X-Hub-Signature-256: sha256=<hex>`, HMAC-SHA256 over the raw body.
- **`jira`** — `X-Hub-Signature: sha256=<hex>`, HMAC-SHA256 over the raw body.
- **`sentry`** — `Sentry-Hook-Signature: <hex>`, HMAC-SHA256 over the raw body.
- **`vercel`** — `x-vercel-signature: <hex>`, HMAC-**SHA1** over the raw body.
- **`linear`** — `Linear-Signature: <hex>`, HMAC-SHA256 over the raw body, **plus** a
  `webhookTimestamp` freshness guard (rejects deliveries older than 1 min — replay protection).
- **`slack`** — `X-Slack-Signature: v0=<hex>`, HMAC-SHA256 over `v0:{ts}:{body}` where `ts` is
  `X-Slack-Request-Timestamp`, **plus** a 5-min timestamp guard.
- **`gitlab`** — `X-Gitlab-Token` shared-secret equality (constant-time).
- **`figma`** — `passcode` field in the JSON body, shared-secret equality (constant-time).

### Secrets

Production renders the per-integration `webhook_secret` from Vault by `ingest_token`
(Canon §13.7, plan §5.3). This scaffold reads it from the environment instead:

```
WEBHOOK_SECRET_<PROVIDER>   # e.g. WEBHOOK_SECRET_GITHUB
```

An absent or empty secret makes verification fail closed. Secrets are never logged.

## Configuration

| Env var                   | Default                     | Meaning |
|---------------------------|-----------------------------|---------|
| `INGESTER_PORT`           | `8090`                      | HTTP listen port |
| `REDIS_URL`               | `redis://localhost:6379/0`  | Redis connection URL (`redis.ParseURL`) |
| `WEBHOOK_SECRET_<PROVIDER>` | _(unset)_                 | Per-provider signing secret (scaffold; Vault in prod) |

## Run locally

```bash
# from this directory
go mod tidy          # generates go.sum (needs network; no Go toolchain is bundled in the plan env)
export REDIS_URL=redis://localhost:6379/0
export WEBHOOK_SECRET_GITHUB=dev-secret
go run .
```

Health check:

```bash
curl -s localhost:8090/healthz            # {"status":"ok"}
```

Send a signed GitHub-style webhook:

```bash
BODY='{"action":"opened","number":1}'
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "dev-secret" | awk '{print $2}')"
curl -s -i -X POST localhost:8090/webhooks/github \
  -H "X-GitHub-Event: pull_request" \
  -H "X-Hub-Signature-256: $SIG" \
  -H "Content-Type: application/json" \
  --data "$BODY"                          # -> 202 Accepted
```

Inspect the stream:

```bash
redis-cli XRANGE ingest:webhooks - + COUNT 5
```

## Build

```bash
go build ./...                            # binary
docker build -t foundry/ingester .       # distroless image, EXPOSE 8090
```

## Notes / next steps

- Add the per-integration `ingest_token` path segment and Vault-backed secret cache
  (plan §5.1, §5.3) — this scaffold keys secrets by provider via env for local dev.
- Add hot-path dedup (`SETNX dedup:{integration}:{delivery_id}` TTL 24h, plan §5.4)
  and `/readyz` + `/metrics` (Prometheus) endpoints (plan §5.1).
