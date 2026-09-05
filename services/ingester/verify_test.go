package main

import (
	"crypto/hmac"
	"crypto/sha1"
	"encoding/hex"
	"net/http"
	"testing"
	"time"
)

var testSecret = []byte("s3cr3t-webhook-key")

func hmacSHA1Hex(secret, body []byte) string {
	mac := hmac.New(sha1.New, secret)
	mac.Write(body)
	return hex.EncodeToString(mac.Sum(nil))
}

// fixedClock pins `now` for the duration of a test so replay guards are deterministic.
func fixedClock(t *testing.T, at time.Time) {
	t.Helper()
	prev := now
	now = func() time.Time { return at }
	t.Cleanup(func() { now = prev })
}

// TestVerifiers_HappyPath asserts each provider accepts a correctly-signed delivery.
func TestVerifiers_HappyPath(t *testing.T) {
	nowTs := time.Unix(1_700_000_000, 0)
	fixedClock(t, nowTs)
	body := []byte(`{"action":"opened"}`)

	cases := []struct {
		name   string
		verify verifier
		header http.Header
		body   []byte
	}{
		{"github", verifyGitHub, hdr("X-Hub-Signature-256", "sha256="+hmacSHA256Hex(testSecret, body)), body},
		{"jira", verifyJira, hdr("X-Hub-Signature", "sha256="+hmacSHA256Hex(testSecret, body)), body},
		{"sentry", verifySentry, hdr("Sentry-Hook-Signature", hmacSHA256Hex(testSecret, body)), body},
		{"vercel", verifyVercel, hdr("x-vercel-signature", hmacSHA1Hex(testSecret, body)), body},
		{"gitlab", verifyGitLab, hdr("X-Gitlab-Token", string(testSecret)), body},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if vd := c.verify(c.header, c.body, testSecret); !vd.ok {
				t.Fatalf("expected ok, got reason=%q", vd.reason)
			}
		})
	}

	// figma: passcode carried in the body.
	figmaBody := []byte(`{"event_type":"PING","passcode":"s3cr3t-webhook-key"}`)
	if vd := verifyFigma(http.Header{}, figmaBody, testSecret); !vd.ok {
		t.Fatalf("figma: expected ok, got reason=%q", vd.reason)
	}

	// slack: signature is HMAC over "v0:{ts}:{body}".
	slackTs := "1700000000"
	base := []byte("v0:" + slackTs + ":")
	base = append(base, body...)
	slackHdr := hdr("X-Slack-Request-Timestamp", slackTs)
	slackHdr.Set("X-Slack-Signature", "v0="+hmacSHA256Hex(testSecret, base))
	if vd := verifySlack(slackHdr, body, testSecret); !vd.ok {
		t.Fatalf("slack: expected ok, got reason=%q", vd.reason)
	}

	// linear: signature over body + fresh webhookTimestamp.
	linearBody := []byte(`{"action":"create","webhookTimestamp":1700000000000}`)
	linearHdr := hdr("Linear-Signature", hmacSHA256Hex(testSecret, linearBody))
	if vd := verifyLinear(linearHdr, linearBody, testSecret); !vd.ok {
		t.Fatalf("linear: expected ok, got reason=%q", vd.reason)
	}
}

// TestVerifiers_FailClosed asserts tampering, wrong secrets, and stale timestamps are rejected.
func TestVerifiers_FailClosed(t *testing.T) {
	fixedClock(t, time.Unix(1_700_000_000, 0))
	body := []byte(`{"action":"opened"}`)

	// Tampered body → signature mismatch.
	sig := "sha256=" + hmacSHA256Hex(testSecret, body)
	if vd := verifyGitHub(hdr("X-Hub-Signature-256", sig), []byte(`{"action":"closed"}`), testSecret); vd.ok {
		t.Fatal("github: tampered body must be rejected")
	}

	// Missing secret → fail closed.
	if vd := verifyGitHub(hdr("X-Hub-Signature-256", sig), body, nil); vd.ok {
		t.Fatal("github: missing secret must be rejected")
	}

	// gitlab wrong token.
	if vd := verifyGitLab(hdr("X-Gitlab-Token", "wrong"), body, testSecret); vd.ok {
		t.Fatal("gitlab: wrong token must be rejected")
	}

	// figma wrong passcode.
	if vd := verifyFigma(http.Header{}, []byte(`{"passcode":"nope"}`), testSecret); vd.ok {
		t.Fatal("figma: wrong passcode must be rejected")
	}

	// slack stale timestamp (10 min old) with an otherwise-valid signature.
	staleTs := "1699999400" // 600s before now
	base := append([]byte("v0:"+staleTs+":"), body...)
	sh := hdr("X-Slack-Request-Timestamp", staleTs)
	sh.Set("X-Slack-Signature", "v0="+hmacSHA256Hex(testSecret, base))
	if vd := verifySlack(sh, body, testSecret); vd.ok {
		t.Fatal("slack: stale timestamp must be rejected")
	}

	// linear stale webhookTimestamp (2 min old).
	lb := []byte(`{"action":"create","webhookTimestamp":1699999880000}`)
	if vd := verifyLinear(hdr("Linear-Signature", hmacSHA256Hex(testSecret, lb)), lb, testSecret); vd.ok {
		t.Fatal("linear: stale timestamp must be rejected")
	}
}

func hdr(k, v string) http.Header {
	h := http.Header{}
	h.Set(k, v)
	return h
}
