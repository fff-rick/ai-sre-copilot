package gateway

import (
	"context"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"

	toolgatewayv1 "ai-sre-copilot.local/tool-gateway/gen/ai/sre/toolgateway/v1"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/timestamppb"
)

const (
	defaultLimit = uint32(100)
	maximumLimit = uint32(1000)
	maximumRange = 7 * 24 * time.Hour
)

var (
	traceIDPattern  = regexp.MustCompile(`^[a-fA-F0-9]{16,64}$`)
	revisionPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._/~^-]{0,199}$`)
)

type serverOptions struct {
	Observability observabilityConnector
	Releases      releaseConnector
	Git           gitConnector
	Kubernetes    kubernetesConnector
	Limiter       *actorLimiter
	Audit         auditSink
	Artifacts     *artifactStore
	InlineBytes   int
	AuthToken     string
}

// Server implements the fixed V1 read-only tool surface.
type Server struct {
	toolgatewayv1.UnimplementedToolGatewayV1Server
	observability observabilityConnector
	releases      releaseConnector
	git           gitConnector
	kubernetes    kubernetesConnector
	limiter       *actorLimiter
	audit         auditSink
	artifacts     *artifactStore
	inlineBytes   int
	authToken     string
}

func newServer(options serverOptions) (*Server, error) {
	if options.Observability == nil || options.Releases == nil || options.Git == nil || options.Kubernetes == nil || options.Limiter == nil || options.Audit == nil || options.Artifacts == nil {
		return nil, errors.New("all gateway dependencies are required")
	}
	if options.InlineBytes <= 0 || options.AuthToken == "" {
		return nil, errors.New("inline response size and authentication token are required")
	}
	return &Server{
		observability: options.Observability, releases: options.Releases, git: options.Git,
		kubernetes: options.Kubernetes, limiter: options.Limiter, audit: options.Audit,
		artifacts: options.Artifacts, inlineBytes: options.InlineBytes, authToken: options.AuthToken,
	}, nil
}

func (s *Server) ListTools(ctx context.Context, request *toolgatewayv1.ListToolsRequest) (*toolgatewayv1.ListToolsResponse, error) {
	if err := validateContext(ctx, request.GetContext(), s.limiter, s.authToken); err != nil {
		return nil, toStatus(err)
	}
	return &toolgatewayv1.ListToolsResponse{Tools: []*toolgatewayv1.ToolDescriptor{
		{Name: "prometheus.query", Version: "v1", Description: "Run a bounded PromQL instant query."},
		{Name: "loki.query_range", Version: "v1", Description: "Run a bounded LogQL range query."},
		{Name: "tempo.get_trace", Version: "v1", Description: "Fetch one trace by trace ID."},
		{Name: "tempo.search_traces", Version: "v1", Description: "Run a bounded TraceQL search."},
		{Name: "releases.list", Version: "v1", Description: "List bounded release records for a service."},
		{Name: "git.get_commit", Version: "v1", Description: "Read commit metadata and bounded file statistics."},
		{Name: "kubernetes.get_workload", Version: "v1", Description: "Read one supported workload status."},
		{Name: "kubernetes.list_events", Version: "v1", Description: "List bounded Kubernetes events."},
	}}, nil
}

func (s *Server) QueryPrometheus(ctx context.Context, request *toolgatewayv1.QueryPrometheusRequest) (*toolgatewayv1.ReadToolResponse, error) {
	args := request.GetArgs()
	if err := validateContext(ctx, request.GetContext(), s.limiter, s.authToken); err != nil {
		return nil, s.fail(ctx, request.GetContext(), "prometheus.query", args, err, time.Now())
	}
	if args == nil || strings.TrimSpace(args.GetPromql()) == "" || len(args.GetPromql()) > 4000 {
		return nil, s.fail(ctx, request.GetContext(), "prometheus.query", args, invalid("promql must contain 1 to 4000 characters"), time.Now())
	}
	at, err := optionalTime(args.GetAt())
	if err != nil {
		return nil, s.fail(ctx, request.GetContext(), "prometheus.query", args, err, time.Now())
	}
	return s.execute(ctx, request.GetContext(), "prometheus.query", args, func() (connectorResult, error) {
		return s.observability.QueryPrometheus(ctx, args.GetPromql(), at)
	})
}

func (s *Server) QueryLoki(ctx context.Context, request *toolgatewayv1.QueryLokiRequest) (*toolgatewayv1.ReadToolResponse, error) {
	args := request.GetArgs()
	if err := validateContext(ctx, request.GetContext(), s.limiter, s.authToken); err != nil {
		return nil, s.fail(ctx, request.GetContext(), "loki.query_range", args, err, time.Now())
	}
	if args == nil || strings.TrimSpace(args.GetLogql()) == "" || len(args.GetLogql()) > 4000 {
		return nil, s.fail(ctx, request.GetContext(), "loki.query_range", args, invalid("logql must contain 1 to 4000 characters"), time.Now())
	}
	start, end, err := requiredRange(args.GetStart(), args.GetEnd())
	if err != nil {
		return nil, s.fail(ctx, request.GetContext(), "loki.query_range", args, err, time.Now())
	}
	limit, err := boundedLimit(args.GetLimit())
	if err != nil {
		return nil, s.fail(ctx, request.GetContext(), "loki.query_range", args, err, time.Now())
	}
	direction := strings.ToLower(args.GetDirection())
	if direction == "" {
		direction = "backward"
	}
	if direction != "forward" && direction != "backward" {
		return nil, s.fail(ctx, request.GetContext(), "loki.query_range", args, invalid("direction must be forward or backward"), time.Now())
	}
	return s.execute(ctx, request.GetContext(), "loki.query_range", args, func() (connectorResult, error) {
		return s.observability.QueryLoki(ctx, args.GetLogql(), start, end, limit, direction)
	})
}

func (s *Server) GetTempoTrace(ctx context.Context, request *toolgatewayv1.GetTempoTraceRequest) (*toolgatewayv1.ReadToolResponse, error) {
	args := request.GetArgs()
	if err := validateContext(ctx, request.GetContext(), s.limiter, s.authToken); err != nil {
		return nil, s.fail(ctx, request.GetContext(), "tempo.get_trace", args, err, time.Now())
	}
	if args == nil || !traceIDPattern.MatchString(args.GetTraceId()) {
		return nil, s.fail(ctx, request.GetContext(), "tempo.get_trace", args, invalid("trace_id must be 16 to 64 hexadecimal characters"), time.Now())
	}
	return s.execute(ctx, request.GetContext(), "tempo.get_trace", args, func() (connectorResult, error) {
		return s.observability.GetTempoTrace(ctx, args.GetTraceId())
	})
}

func (s *Server) SearchTempoTraces(ctx context.Context, request *toolgatewayv1.SearchTempoTracesRequest) (*toolgatewayv1.ReadToolResponse, error) {
	args := request.GetArgs()
	if err := validateContext(ctx, request.GetContext(), s.limiter, s.authToken); err != nil {
		return nil, s.fail(ctx, request.GetContext(), "tempo.search_traces", args, err, time.Now())
	}
	if args == nil || strings.TrimSpace(args.GetTraceql()) == "" || len(args.GetTraceql()) > 4000 {
		return nil, s.fail(ctx, request.GetContext(), "tempo.search_traces", args, invalid("traceql must contain 1 to 4000 characters"), time.Now())
	}
	start, end, err := requiredRange(args.GetStart(), args.GetEnd())
	if err != nil {
		return nil, s.fail(ctx, request.GetContext(), "tempo.search_traces", args, err, time.Now())
	}
	limit, err := boundedLimit(args.GetLimit())
	if err != nil {
		return nil, s.fail(ctx, request.GetContext(), "tempo.search_traces", args, err, time.Now())
	}
	return s.execute(ctx, request.GetContext(), "tempo.search_traces", args, func() (connectorResult, error) {
		return s.observability.SearchTempoTraces(ctx, args.GetTraceql(), start, end, limit)
	})
}

func (s *Server) ListReleases(ctx context.Context, request *toolgatewayv1.ListReleasesRequest) (*toolgatewayv1.ReadToolResponse, error) {
	args := request.GetArgs()
	if err := validateContext(ctx, request.GetContext(), s.limiter, s.authToken); err != nil {
		return nil, s.fail(ctx, request.GetContext(), "releases.list", args, err, time.Now())
	}
	if args == nil || !kubernetesNamePattern.MatchString(args.GetService()) {
		return nil, s.fail(ctx, request.GetContext(), "releases.list", args, invalid("service must be a valid service name"), time.Now())
	}
	start, end, err := requiredRange(args.GetStart(), args.GetEnd())
	if err != nil {
		return nil, s.fail(ctx, request.GetContext(), "releases.list", args, err, time.Now())
	}
	limit, err := boundedLimit(args.GetLimit())
	if err != nil {
		return nil, s.fail(ctx, request.GetContext(), "releases.list", args, err, time.Now())
	}
	return s.execute(ctx, request.GetContext(), "releases.list", args, func() (connectorResult, error) {
		return s.releases.ListReleases(ctx, args.GetService(), start, end, limit)
	})
}

func (s *Server) GetGitCommit(ctx context.Context, request *toolgatewayv1.GetGitCommitRequest) (*toolgatewayv1.ReadToolResponse, error) {
	args := request.GetArgs()
	if err := validateContext(ctx, request.GetContext(), s.limiter, s.authToken); err != nil {
		return nil, s.fail(ctx, request.GetContext(), "git.get_commit", args, err, time.Now())
	}
	if args == nil || !revisionPattern.MatchString(args.GetRevision()) {
		return nil, s.fail(ctx, request.GetContext(), "git.get_commit", args, invalid("revision contains unsupported characters"), time.Now())
	}
	limit, err := boundedLimit(args.GetMaxChangedFiles())
	if err != nil {
		return nil, s.fail(ctx, request.GetContext(), "git.get_commit", args, err, time.Now())
	}
	return s.execute(ctx, request.GetContext(), "git.get_commit", args, func() (connectorResult, error) {
		return s.git.GetCommit(ctx, args.GetRevision(), limit)
	})
}

func (s *Server) GetKubernetesWorkload(ctx context.Context, request *toolgatewayv1.GetKubernetesWorkloadRequest) (*toolgatewayv1.ReadToolResponse, error) {
	args := request.GetArgs()
	if err := validateContext(ctx, request.GetContext(), s.limiter, s.authToken); err != nil {
		return nil, s.fail(ctx, request.GetContext(), "kubernetes.get_workload", args, err, time.Now())
	}
	if args == nil || !kubernetesNamePattern.MatchString(args.GetNamespace()) || !kubernetesNamePattern.MatchString(args.GetName()) {
		return nil, s.fail(ctx, request.GetContext(), "kubernetes.get_workload", args, invalid("namespace and name must be valid Kubernetes names"), time.Now())
	}
	return s.execute(ctx, request.GetContext(), "kubernetes.get_workload", args, func() (connectorResult, error) {
		return s.kubernetes.GetWorkload(ctx, args.GetNamespace(), args.GetKind(), args.GetName())
	})
}

func (s *Server) ListKubernetesEvents(ctx context.Context, request *toolgatewayv1.ListKubernetesEventsRequest) (*toolgatewayv1.ReadToolResponse, error) {
	args := request.GetArgs()
	if err := validateContext(ctx, request.GetContext(), s.limiter, s.authToken); err != nil {
		return nil, s.fail(ctx, request.GetContext(), "kubernetes.list_events", args, err, time.Now())
	}
	if args == nil || !kubernetesNamePattern.MatchString(args.GetNamespace()) || (args.GetInvolvedObjectName() != "" && !kubernetesNamePattern.MatchString(args.GetInvolvedObjectName())) {
		return nil, s.fail(ctx, request.GetContext(), "kubernetes.list_events", args, invalid("namespace or involved object name is invalid"), time.Now())
	}
	limit, err := boundedLimit(args.GetLimit())
	if err != nil {
		return nil, s.fail(ctx, request.GetContext(), "kubernetes.list_events", args, err, time.Now())
	}
	return s.execute(ctx, request.GetContext(), "kubernetes.list_events", args, func() (connectorResult, error) {
		return s.kubernetes.ListEvents(ctx, args.GetNamespace(), args.GetInvolvedObjectKind(), args.GetInvolvedObjectName(), limit)
	})
}

func (s *Server) execute(ctx context.Context, requestContext *toolgatewayv1.RequestContext, toolName string, args proto.Message, call func() (connectorResult, error)) (*toolgatewayv1.ReadToolResponse, error) {
	started := time.Now()
	result, err := call()
	if err != nil {
		if errors.Is(ctx.Err(), context.DeadlineExceeded) || errors.Is(err, context.DeadlineExceeded) {
			err = classified(toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_DEADLINE_EXCEEDED, "tool deadline exceeded", true, err)
		}
		return nil, s.fail(ctx, requestContext, toolName, args, err, started)
	}
	payload, redacted, err := sanitizePayload(result.Data)
	if err != nil {
		return nil, s.fail(ctx, requestContext, toolName, args, err, started)
	}
	response := &toolgatewayv1.ReadToolResponse{ToolName: toolName, SourceRef: result.SourceRef, Redacted: redacted}
	if len(payload) <= s.inlineBytes {
		response.JsonPayload = payload
	} else {
		response.Artifact, err = s.artifacts.Put(payload)
		if err != nil {
			return nil, s.fail(ctx, requestContext, toolName, args, err, started)
		}
	}
	s.writeAudit(ctx, requestContext, toolName, args, "success", "", started)
	return response, nil
}

func (s *Server) fail(ctx context.Context, requestContext *toolgatewayv1.RequestContext, toolName string, args proto.Message, err error, started time.Time) error {
	s.writeAudit(ctx, requestContext, toolName, args, "error", errorCode(err), started)
	return toStatus(err)
}

func (s *Server) writeAudit(ctx context.Context, requestContext *toolgatewayv1.RequestContext, toolName string, args proto.Message, outcome, code string, started time.Time) {
	event := auditEvent{Timestamp: nowUTC(), Tool: toolName, Outcome: outcome, ErrorCode: code, DurationMS: time.Since(started).Milliseconds()}
	if requestContext != nil {
		event.InvestigationID, event.TraceID = requestContext.GetInvestigationId(), requestContext.GetTraceId()
		if requestContext.GetCaller() != nil {
			event.ActorID, event.Role = requestContext.GetCaller().GetActorId(), requestContext.GetCaller().GetRole()
		}
	}
	if args != nil {
		event.ArgumentsSHA256 = argumentHash(args)
	}
	s.audit.Write(ctx, event)
}

func optionalTime(value *timestamppb.Timestamp) (time.Time, error) {
	if value == nil {
		return time.Time{}, nil
	}
	if !value.IsValid() {
		return time.Time{}, invalid("timestamp is invalid")
	}
	return value.AsTime(), nil
}

func requiredRange(startValue, endValue *timestamppb.Timestamp) (time.Time, time.Time, error) {
	if startValue == nil || endValue == nil || !startValue.IsValid() || !endValue.IsValid() {
		return time.Time{}, time.Time{}, invalid("valid start and end timestamps are required")
	}
	start, end := startValue.AsTime(), endValue.AsTime()
	if !start.Before(end) || end.Sub(start) > maximumRange {
		return time.Time{}, time.Time{}, invalid(fmt.Sprintf("time range must be positive and no longer than %s", maximumRange))
	}
	return start, end, nil
}

func boundedLimit(value uint32) (uint32, error) {
	if value == 0 {
		return defaultLimit, nil
	}
	if value > maximumLimit {
		return 0, invalid(fmt.Sprintf("limit must not exceed %d", maximumLimit))
	}
	return value, nil
}
