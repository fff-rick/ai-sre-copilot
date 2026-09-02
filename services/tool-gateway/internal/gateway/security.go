package gateway

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	toolgatewayv1 "ai-sre-copilot.local/tool-gateway/gen/ai/sre/toolgateway/v1"
	"golang.org/x/time/rate"
	"google.golang.org/grpc/metadata"
	"google.golang.org/protobuf/proto"
)

var (
	secretKeyPattern = regexp.MustCompile(`(?i)(authorization|password|passwd|secret|token|api[_-]?key|private[_-]?key)`)
	bearerPattern    = regexp.MustCompile(`(?i)bearer\s+[a-z0-9._~+/=-]+`)
)

type actorLimiter struct {
	mu      sync.Mutex
	entries map[string]*rate.Limiter
	rate    rate.Limit
	burst   int
}

func newActorLimiter(requestsPerSecond float64, burst int) *actorLimiter {
	return &actorLimiter{entries: make(map[string]*rate.Limiter), rate: rate.Limit(requestsPerSecond), burst: burst}
}

func (l *actorLimiter) Allow(actor string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	entry := l.entries[actor]
	if entry == nil {
		entry = rate.NewLimiter(l.rate, l.burst)
		l.entries[actor] = entry
	}
	return entry.Allow()
}

type auditEvent struct {
	Timestamp       string `json:"timestamp"`
	Tool            string `json:"tool"`
	InvestigationID string `json:"investigation_id"`
	TraceID         string `json:"trace_id"`
	ActorID         string `json:"actor_id"`
	Role            string `json:"role"`
	ArgumentsSHA256 string `json:"arguments_sha256"`
	Outcome         string `json:"outcome"`
	ErrorCode       string `json:"error_code,omitempty"`
	DurationMS      int64  `json:"duration_ms"`
}

type auditSink interface {
	Write(context.Context, auditEvent)
}

type logAuditSink struct{ logger *slog.Logger }

func (s logAuditSink) Write(_ context.Context, event auditEvent) {
	s.logger.Info("tool_audit",
		"timestamp", event.Timestamp, "tool", event.Tool,
		"investigation_id", event.InvestigationID, "trace_id", event.TraceID,
		"actor_id", event.ActorID, "role", event.Role,
		"arguments_sha256", event.ArgumentsSHA256, "outcome", event.Outcome,
		"error_code", event.ErrorCode, "duration_ms", event.DurationMS)
}

type artifactStore struct {
	directory string
	maxBytes  int
}

func newArtifactStore(directory string, maxBytes int) (*artifactStore, error) {
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return nil, fmt.Errorf("create artifact directory: %w", err)
	}
	return &artifactStore{directory: directory, maxBytes: maxBytes}, nil
}

func (s *artifactStore) Put(data []byte) (*toolgatewayv1.ArtifactReference, error) {
	if len(data) > s.maxBytes {
		return nil, classified(toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_RESPONSE_TOO_LARGE, "tool response exceeds the maximum size", false, nil)
	}
	hash := sha256.Sum256(data)
	digest := hex.EncodeToString(hash[:])
	path := filepath.Join(s.directory, digest+".json")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return nil, fmt.Errorf("write artifact: %w", err)
	}
	return &toolgatewayv1.ArtifactReference{Uri: "artifact://" + digest, Sha256: digest, SizeBytes: uint64(len(data))}, nil
}

func argumentHash(message proto.Message) string {
	data, err := proto.MarshalOptions{Deterministic: true}.Marshal(message)
	if err != nil {
		return "marshal-error"
	}
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:])
}

func redactJSON(value any) (any, bool) {
	switch typed := value.(type) {
	case map[string]any:
		redacted := false
		for key, child := range typed {
			if secretKeyPattern.MatchString(key) {
				typed[key] = "[REDACTED]"
				redacted = true
				continue
			}
			var childRedacted bool
			typed[key], childRedacted = redactJSON(child)
			redacted = redacted || childRedacted
		}
		return typed, redacted
	case []any:
		redacted := false
		for index, child := range typed {
			var childRedacted bool
			typed[index], childRedacted = redactJSON(child)
			redacted = redacted || childRedacted
		}
		return typed, redacted
	case string:
		clean := bearerPattern.ReplaceAllString(typed, "Bearer [REDACTED]")
		return clean, clean != typed
	default:
		return value, false
	}
}

func sanitizePayload(value any) ([]byte, bool, error) {
	encoded, err := json.Marshal(value)
	if err != nil {
		return nil, false, fmt.Errorf("encode connector result: %w", err)
	}
	var normalized any
	if err := json.Unmarshal(encoded, &normalized); err != nil {
		return nil, false, fmt.Errorf("normalize connector result: %w", err)
	}
	normalized, redacted := redactJSON(normalized)
	encoded, err = json.Marshal(normalized)
	return encoded, redacted, err
}

func validateContext(ctx context.Context, requestContext *toolgatewayv1.RequestContext, limiter *actorLimiter, authToken string) error {
	if _, ok := ctx.Deadline(); !ok {
		return invalid("gRPC deadline is required")
	}
	values := metadata.ValueFromIncomingContext(ctx, "authorization")
	want := "Bearer " + authToken
	if len(values) != 1 || subtle.ConstantTimeCompare([]byte(values[0]), []byte(want)) != 1 {
		return classified(toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_UNAUTHENTICATED, "valid gateway credentials are required", false, nil)
	}
	if requestContext == nil || strings.TrimSpace(requestContext.GetInvestigationId()) == "" || len(requestContext.GetInvestigationId()) > 255 {
		return invalid("investigation_id is required")
	}
	if strings.TrimSpace(requestContext.GetTraceId()) == "" || len(requestContext.GetTraceId()) > 128 {
		return invalid("trace_id is required")
	}
	caller := requestContext.GetCaller()
	if caller == nil || strings.TrimSpace(caller.GetActorId()) == "" {
		return classified(toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_UNAUTHENTICATED, "caller identity is required", false, nil)
	}
	if caller.GetRole() != "investigator" && caller.GetRole() != "admin" {
		return classified(toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_PERMISSION_DENIED, "caller role is not allowed", false, nil)
	}
	if !limiter.Allow(caller.GetActorId()) {
		return classified(toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_RATE_LIMITED, "caller rate limit exceeded", true, nil)
	}
	return nil
}

func errorCode(err error) string {
	var domain *Error
	if errors.As(err, &domain) {
		return domain.Code.String()
	}
	return toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_INTERNAL.String()
}

func nowUTC() string { return time.Now().UTC().Format(time.RFC3339Nano) }
