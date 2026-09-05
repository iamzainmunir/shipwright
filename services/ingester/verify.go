package main

import (
	"crypto/hmac"
	"crypto/sha1"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"hash"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// verdict is the outcome of a signature check.
type verdict struct {
	ok     bool
	reason string // machine-readable, e.g. "signature_mismatch"; never contains secrets
}

// verifier validates a provider's webhook signature over the RAW request body.
// It MUST use a constant-time comparison (hmac.Equal / subtle.ConstantTimeCompare)
// for any secret-derived value. Ported from plan §5.3.
type verifier func(h http.Header, body, secret []byte) verdict

// now is overridable in tests so the Slack/Linear replay guards are deterministic.
var now = time.Now

// Replay windows: a delivery whose timestamp is further from now than this is
// rejected even when the signature is valid, to blunt replay attacks (plan §5.3).
const (
	slackMaxSkew  = 5 * time.Minute
	linearMaxSkew = 1 * time.Minute
)

// verifiers registers a verifier per Canon integration kind that emits webhooks.
// Every provider fails closed: an unimplemented or failing check never accepts a delivery.
var verifiers = map[string]verifier{
	"github": verifyGitHub, // X-Hub-Signature-256: sha256=<hex> HMAC-SHA256
	"gitlab": verifyGitLab, // X-Gitlab-Token equality
	"jira":   verifyJira,   // X-Hub-Signature: sha256=<hex> HMAC-SHA256
	"linear": verifyLinear, // Linear-Signature HMAC-SHA256 + webhookTimestamp freshness
	"slack":  verifySlack,  // X-Slack-Signature v0 over "v0:{ts}:{body}" + timestamp guard
	"sentry": verifySentry, // Sentry-Hook-Signature HMAC-SHA256 (hex)
	"vercel": verifyVercel, // x-vercel-signature HMAC-SHA1 (hex)
	"figma":  verifyFigma,  // body passcode equality
}

// verifierFor returns the verifier registered for a provider.
func verifierFor(provider string) (verifier, bool) {
	v, ok := verifiers[provider]
	return v, ok
}

// --- HMAC-over-body providers (differ only by header name and hash) ----------

// verifyGitHub validates X-Hub-Signature-256: "sha256=<hex>" = HMAC-SHA256(secret, body).
func verifyGitHub(h http.Header, body, secret []byte) verdict {
	return verifyHexHMAC(sha256.New, h.Get("X-Hub-Signature-256"), "sha256=", body, secret)
}

// verifyJira validates X-Hub-Signature: "sha256=<hex>" = HMAC-SHA256(secret, body).
// Jira sends the same GitHub-style header (without the "-256" suffix) when a secret is set.
func verifyJira(h http.Header, body, secret []byte) verdict {
	return verifyHexHMAC(sha256.New, h.Get("X-Hub-Signature"), "sha256=", body, secret)
}

// verifyLinear validates Linear-Signature (bare hex HMAC-SHA256 over the raw body) and then
// checks the body's webhookTimestamp is fresh, so an intercepted delivery cannot be replayed.
func verifyLinear(h http.Header, body, secret []byte) verdict {
	if vd := verifyHexHMAC(sha256.New, h.Get("Linear-Signature"), "", body, secret); !vd.ok {
		return vd
	}
	var payload struct {
		WebhookTimestamp int64 `json:"webhookTimestamp"` // milliseconds since epoch
	}
	if err := json.Unmarshal(body, &payload); err != nil || payload.WebhookTimestamp == 0 {
		return verdict{ok: false, reason: "missing_timestamp"}
	}
	ts := time.UnixMilli(payload.WebhookTimestamp)
	if absDuration(now().Sub(ts)) > linearMaxSkew {
		return verdict{ok: false, reason: "stale_timestamp"}
	}
	return verdict{ok: true}
}

// verifySentry validates Sentry-Hook-Signature: bare hex HMAC-SHA256 over the raw body.
func verifySentry(h http.Header, body, secret []byte) verdict {
	return verifyHexHMAC(sha256.New, h.Get("Sentry-Hook-Signature"), "", body, secret)
}

// verifyVercel validates x-vercel-signature: bare hex HMAC-SHA1 over the raw body.
func verifyVercel(h http.Header, body, secret []byte) verdict {
	return verifyHexHMAC(sha1.New, h.Get("x-vercel-signature"), "", body, secret)
}

// --- shared-secret / passcode providers --------------------------------------

// verifyGitLab validates X-Gitlab-Token: a shared secret compared in constant time.
func verifyGitLab(h http.Header, _, secret []byte) verdict {
	return verifyEquality("X-Gitlab-Token", []byte(h.Get("X-Gitlab-Token")), secret)
}

// verifyFigma validates the "passcode" field in the JSON body against the shared secret.
func verifyFigma(_ http.Header, body, secret []byte) verdict {
	var payload struct {
		Passcode string `json:"passcode"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		return verdict{ok: false, reason: "invalid_body"}
	}
	return verifyEquality("passcode", []byte(payload.Passcode), secret)
}

// --- Slack (signs a timestamp-prefixed base string) --------------------------

// verifySlack validates X-Slack-Signature: "v0=<hex>" = HMAC-SHA256(secret, "v0:{ts}:{body}")
// where ts is the X-Slack-Request-Timestamp header. The timestamp is guarded against replay
// before the signature is checked (Slack's documented scheme, plan §5.3).
func verifySlack(h http.Header, body, secret []byte) verdict {
	if len(secret) == 0 {
		return verdict{ok: false, reason: "missing_secret"}
	}
	tsHeader := h.Get("X-Slack-Request-Timestamp")
	if tsHeader == "" {
		return verdict{ok: false, reason: "missing_timestamp"}
	}
	tsSec, err := strconv.ParseInt(tsHeader, 10, 64)
	if err != nil {
		return verdict{ok: false, reason: "bad_timestamp"}
	}
	if absDuration(now().Sub(time.Unix(tsSec, 0))) > slackMaxSkew {
		return verdict{ok: false, reason: "stale_timestamp"}
	}

	got := h.Get("X-Slack-Signature")
	if got == "" {
		return verdict{ok: false, reason: "missing_signature"}
	}
	base := []byte("v0:" + tsHeader + ":")
	base = append(base, body...)
	return verifyHexHMAC(sha256.New, got, "v0=", base, secret)
}

// --- primitives --------------------------------------------------------------

// verifyHexHMAC checks that `header` (an optional prefix followed by lowercase hex) equals
// HMAC(secret, message) using the given hash. Comparison is constant-time (hmac.Equal).
func verifyHexHMAC(newHash func() hash.Hash, header, prefix string, message, secret []byte) verdict {
	if len(secret) == 0 {
		return verdict{ok: false, reason: "missing_secret"}
	}
	if header == "" {
		return verdict{ok: false, reason: "missing_signature"}
	}
	if prefix != "" {
		if !strings.HasPrefix(header, prefix) {
			return verdict{ok: false, reason: "bad_signature_format"}
		}
		header = strings.TrimPrefix(header, prefix)
	}
	gotMAC, err := hex.DecodeString(header)
	if err != nil {
		return verdict{ok: false, reason: "bad_signature_encoding"}
	}
	mac := hmac.New(newHash, secret)
	mac.Write(message)
	if !hmac.Equal(gotMAC, mac.Sum(nil)) {
		return verdict{ok: false, reason: "signature_mismatch"}
	}
	return verdict{ok: true}
}

// verifyEquality compares a presented shared secret to the expected one in constant time.
func verifyEquality(field string, presented, secret []byte) verdict {
	if len(secret) == 0 {
		return verdict{ok: false, reason: "missing_secret"}
	}
	if len(presented) == 0 {
		return verdict{ok: false, reason: "missing_" + field}
	}
	if subtle.ConstantTimeCompare(presented, secret) != 1 {
		return verdict{ok: false, reason: "signature_mismatch"}
	}
	return verdict{ok: true}
}

// absDuration returns the absolute value of a duration.
func absDuration(d time.Duration) time.Duration {
	if d < 0 {
		return -d
	}
	return d
}

// hmacSHA256Hex computes the hex HMAC-SHA256 of body under secret (shared helper).
func hmacSHA256Hex(secret, body []byte) string {
	mac := hmac.New(sha256.New, secret)
	mac.Write(body)
	return hex.EncodeToString(mac.Sum(nil))
}
