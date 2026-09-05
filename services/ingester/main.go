// Command ingester is Shipwright's Go webhook ingester (Canon §4, §13.8).
//
// It accepts inbound provider webhooks, verifies each signature (see verify.go),
// normalizes the delivery into a compact event, and XADDs it to the single Redis
// Stream "ingest:webhooks". The orchestrator consumes that stream via consumer
// group "g_router" (Canon §13.8). The ingester is inbound-only and stateless with
// respect to business logic: it never calls providers back and never touches Postgres.
package main

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"
)

const (
	// streamKey is the single normalized inbound stream (Canon §13.8).
	streamKey = "ingest:webhooks"
	// streamMaxLen caps the stream with approximate trimming (plan §5.5).
	streamMaxLen = 100_000
	// maxBodyBytes bounds an inbound webhook body to protect memory.
	maxBodyBytes = 5 << 20 // 5 MiB
)

// config is the env-driven runtime configuration.
type config struct {
	port     string
	redisURL string
}

func loadConfig() config {
	return config{
		port:     getenv("INGESTER_PORT", "8090"),
		redisURL: getenv("REDIS_URL", "redis://localhost:6379/0"),
	}
}

// event is the normalized transport shape XADDed to Redis. JSON is camelCase
// (Canon §13). It is intentionally compact; the orchestrator router enriches it
// into the full NormalizedInboundEvent (plan §4.1) once the integration is resolved.
type event struct {
	ID         string          `json:"id"`         // ULID (Canon §8)
	Provider   string          `json:"provider"`   // integration kind: github|jira|linear|...
	Kind       string          `json:"kind"`       // provider event type, e.g. "pull_request"
	ReceivedAt time.Time       `json:"receivedAt"` // ingest timestamp (UTC, RFC3339)
	Payload    json.RawMessage `json:"payload"`    // raw provider JSON body
}

// server holds the shared dependencies for the HTTP handlers.
type server struct {
	rdb *redis.Client
	log *slog.Logger
}

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	slog.SetDefault(logger)

	cfg := loadConfig()

	opt, err := redis.ParseURL(cfg.redisURL)
	if err != nil {
		logger.Error("invalid REDIS_URL", "err", err)
		os.Exit(1)
	}
	rdb := redis.NewClient(opt)
	defer func() { _ = rdb.Close() }()

	// Best-effort connectivity probe: the service still starts if Redis is down so
	// that /healthz responds; per-request XADD failures return 503 (plan §5.5).
	pingCtx, cancelPing := context.WithTimeout(context.Background(), 3*time.Second)
	if err := rdb.Ping(pingCtx).Err(); err != nil {
		logger.Warn("redis ping failed at startup; will retry on demand", "err", err)
	} else {
		logger.Info("connected to redis")
	}
	cancelPing()

	srv := &server{rdb: rdb, log: logger}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", srv.handleHealth)
	mux.HandleFunc("POST /webhooks/{provider}", srv.handleWebhook)

	httpSrv := &http.Server{
		Addr:              ":" + cfg.port,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       30 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	// Graceful shutdown on SIGINT/SIGTERM.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	go func() {
		logger.Info("ingester listening", "addr", httpSrv.Addr, "stream", streamKey)
		if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("http server error", "err", err)
			stop()
		}
	}()

	<-ctx.Done()
	logger.Info("shutdown signal received, draining connections")

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := httpSrv.Shutdown(shutdownCtx); err != nil {
		logger.Error("graceful shutdown failed", "err", err)
	}
	logger.Info("ingester stopped")
}

// handleHealth is the liveness probe.
func (s *server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// handleWebhook processes POST /webhooks/{provider}:
//  1. read the raw body,
//  2. verify the provider signature (verify.go),
//  3. normalize into an event,
//  4. XADD to the "ingest:webhooks" stream.
//
// Returns 202 on success, 401 on bad/absent signature, 503 if Redis is unavailable.
func (s *server) handleWebhook(w http.ResponseWriter, r *http.Request) {
	provider := r.PathValue("provider")

	verify, ok := verifierFor(provider)
	if !ok {
		s.log.Warn("unknown provider", "provider", provider)
		s.writeError(w, r, http.StatusNotFound, "unknown_provider",
			"no verifier registered for provider", false)
		return
	}

	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxBodyBytes))
	if err != nil {
		s.log.Warn("failed to read body", "provider", provider, "err", err)
		s.writeError(w, r, http.StatusBadRequest, "invalid_body",
			"could not read request body", false)
		return
	}

	// Secret sourcing: production renders the per-integration webhook_secret from
	// Vault by ingest_token (Canon §13.7, plan §5.3). For this scaffold the secret
	// is read from WEBHOOK_SECRET_<PROVIDER>; absent/invalid secrets fail closed.
	secret := webhookSecret(provider)

	vd := verify(r.Header, body, secret)
	if !vd.ok {
		// Do not log secrets or body; only the machine-readable reason.
		s.log.Warn("signature verification failed", "provider", provider, "reason", vd.reason)
		s.writeError(w, r, http.StatusUnauthorized, "bad_signature",
			"webhook signature verification failed", false)
		return
	}

	ev := newEvent(provider, r.Header, body)

	if err := s.publish(r.Context(), ev); err != nil {
		s.log.Error("failed to publish to stream", "provider", provider, "err", err)
		// Backpressure/redis-down: ask the provider to redeliver (plan §5.5).
		w.Header().Set("Retry-After", "30")
		s.writeError(w, r, http.StatusServiceUnavailable, "stream_unavailable",
			"event bus temporarily unavailable, retry later", true)
		return
	}

	s.log.Info("webhook ingested", "provider", provider, "kind", ev.Kind, "id", ev.ID)
	writeJSON(w, http.StatusAccepted, map[string]string{"id": ev.ID, "status": "accepted"})
}

// publish XADDs the event to the Redis stream with approximate MAXLEN trimming.
func (s *server) publish(ctx context.Context, ev event) error {
	data, err := json.Marshal(ev)
	if err != nil {
		return err
	}
	return s.rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: streamKey,
		MaxLen: streamMaxLen,
		Approx: true, // MAXLEN ~ 100000
		Values: map[string]any{
			"event":    string(data),
			"provider": ev.Provider,
			"kind":     ev.Kind,
		},
	}).Err()
}

// newEvent normalizes a verified delivery into the transport shape.
func newEvent(provider string, h http.Header, body []byte) event {
	var payload json.RawMessage
	if json.Valid(body) {
		payload = json.RawMessage(body)
	} else {
		// Non-JSON body (rare): embed as a JSON string so the event stays valid JSON.
		payload, _ = json.Marshal(string(body))
	}
	return event{
		ID:         newULID(),
		Provider:   provider,
		Kind:       eventKind(provider, h),
		ReceivedAt: time.Now().UTC(),
		Payload:    payload,
	}
}

// eventKind extracts the provider-specific event type from headers where the
// provider exposes one. Body-only schemes (e.g. Linear) resolve to "unknown"
// here and are refined by the orchestrator router.
func eventKind(provider string, h http.Header) string {
	switch provider {
	case "github":
		if e := h.Get("X-GitHub-Event"); e != "" {
			return e
		}
	case "gitlab":
		if e := h.Get("X-Gitlab-Event"); e != "" {
			return e
		}
	}
	return "unknown"
}

// webhookSecret loads the signing secret for a provider from the environment.
// Placeholder for the Vault-backed lookup by ingest_token (plan §5.3).
func webhookSecret(provider string) []byte {
	return []byte(os.Getenv("WEBHOOK_SECRET_" + strings.ToUpper(provider)))
}

// writeJSON writes a JSON response with the given status code.
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// writeError emits the Canon §13.5 error envelope:
// { "error": { code, message, details, requestId, retryable } }.
func (s *server) writeError(w http.ResponseWriter, r *http.Request, status int, code, message string, retryable bool) {
	reqID := r.Header.Get("X-Request-Id")
	if reqID == "" {
		reqID = newULID()
	}
	writeJSON(w, status, map[string]any{
		"error": map[string]any{
			"code":      code,
			"message":   message,
			"details":   map[string]any{},
			"requestId": reqID,
			"retryable": retryable,
		},
	})
}

// getenv returns the value of key or def when unset/empty.
func getenv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// crockford is the Crockford base32 alphabet used by ULID encoding.
const crockford = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

// newULID returns a lexicographically sortable 26-char ULID (Canon §8):
// 48-bit millisecond timestamp + 80 bits of randomness, Crockford base32.
func newULID() string {
	var b [16]byte
	ms := uint64(time.Now().UnixMilli())
	b[0] = byte(ms >> 40)
	b[1] = byte(ms >> 32)
	b[2] = byte(ms >> 24)
	b[3] = byte(ms >> 16)
	b[4] = byte(ms >> 8)
	b[5] = byte(ms)
	if _, err := rand.Read(b[6:]); err != nil {
		// crypto/rand should never fail; fall back to a time-derived fill.
		for i := 6; i < 16; i++ {
			b[i] = byte(ms >> uint(8*(i%6)))
		}
	}

	var enc [26]byte
	enc[0] = crockford[(b[0]&224)>>5]
	enc[1] = crockford[b[0]&31]
	enc[2] = crockford[(b[1]&248)>>3]
	enc[3] = crockford[((b[1]&7)<<2)|((b[2]&192)>>6)]
	enc[4] = crockford[(b[2]&62)>>1]
	enc[5] = crockford[((b[2]&1)<<4)|((b[3]&240)>>4)]
	enc[6] = crockford[((b[3]&15)<<1)|((b[4]&128)>>7)]
	enc[7] = crockford[(b[4]&124)>>2]
	enc[8] = crockford[((b[4]&3)<<3)|((b[5]&224)>>5)]
	enc[9] = crockford[b[5]&31]
	enc[10] = crockford[(b[6]&248)>>3]
	enc[11] = crockford[((b[6]&7)<<2)|((b[7]&192)>>6)]
	enc[12] = crockford[(b[7]&62)>>1]
	enc[13] = crockford[((b[7]&1)<<4)|((b[8]&240)>>4)]
	enc[14] = crockford[((b[8]&15)<<1)|((b[9]&128)>>7)]
	enc[15] = crockford[(b[9]&124)>>2]
	enc[16] = crockford[((b[9]&3)<<3)|((b[10]&224)>>5)]
	enc[17] = crockford[b[10]&31]
	enc[18] = crockford[(b[11]&248)>>3]
	enc[19] = crockford[((b[11]&7)<<2)|((b[12]&192)>>6)]
	enc[20] = crockford[(b[12]&62)>>1]
	enc[21] = crockford[((b[12]&1)<<4)|((b[13]&240)>>4)]
	enc[22] = crockford[((b[13]&15)<<1)|((b[14]&128)>>7)]
	enc[23] = crockford[(b[14]&124)>>2]
	enc[24] = crockford[((b[14]&3)<<3)|((b[15]&224)>>5)]
	enc[25] = crockford[b[15]&31]
	return string(enc[:])
}
