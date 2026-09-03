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
	defaultLimit    = uint32(100)
	maximumLimit    = uint32(1000)
	maximumRange    = 7 * 24 * time.Hour
	maximumReplicas = int32(100)
)

var (
	traceIDPattern     = regexp.MustCompile(`^[a-fA-F0-9]{16,64}$`)
	revisionPattern    = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._/~^-]{0,199}$`)
	idempotencyPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$`)
)

type serverOptions struct {
	Observability      observabilityConnector
	Releases           releaseConnector
	Git                gitConnector
	Kubernetes         kubernetesConnector
	Limiter            *actorLimiter
	Audit              auditSink
	Artifacts          *artifactStore
	InlineBytes        int
	AuthToken          string
	MutationAuthorizer mutationAuthorizer
	AllowedNamespace   string
}

// Server implements fixed V1 reads and approval-gated typed mutations.
type Server struct {
	toolgatewayv1.UnimplementedToolGatewayV1Server
	observability      observabilityConnector
	releases           releaseConnector
	git                gitConnector
	kubernetes         kubernetesConnector
	limiter            *actorLimiter
	audit              auditSink
	artifacts          *artifactStore
	inlineBytes        int
	authToken          string
	mutationAuthorizer mutationAuthorizer
	allowedNamespace   string
}

func newServer(options serverOptions) (*Server, error) {
	if options.Observability == nil || options.Releases == nil || options.Git == nil || options.Kubernetes == nil || options.Limiter == nil || options.Audit == nil || options.Artifacts == nil {
		return nil, errors.New("all gateway dependencies are required")
	}
	if options.InlineBytes <= 0 || options.AuthToken == "" {
		return nil, errors.New("inline response size and authentication token are required")
	}
	if options.MutationAuthorizer == nil {
		options.MutationAuthorizer = unavailableMutationAuthorizer{}
	}
	if options.AllowedNamespace == "" {
		options.AllowedNamespace = "ai-sre-test"
	}
	return &Server{
		observability: options.Observability, releases: options.Releases, git: options.Git,
		kubernetes: options.Kubernetes, limiter: options.Limiter, audit: options.Audit,
		artifacts: options.Artifacts, inlineBytes: options.InlineBytes, authToken: options.AuthToken,
		mutationAuthorizer: options.MutationAuthorizer, allowedNamespace: options.AllowedNamespace,
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
		{Name: "kubernetes.restart_deployment", Version: "v1", Description: "Restart one approved Deployment in the isolated namespace."},
		{Name: "kubernetes.scale_deployment", Version: "v1", Description: "Scale one approved Deployment in the isolated namespace."},
		{Name: "kubernetes.rollback_deployment", Version: "v1", Description: "Roll back one approved Deployment revision in the isolated namespace."},
	}}, nil
}

func (s *Server) ExecuteApprovedMutation(ctx context.Context, request *toolgatewayv1.ExecuteApprovedMutationRequest) (*toolgatewayv1.MutationExecution, error) {
	started := time.Now()
	if request == nil {
		return nil, toStatus(invalid("mutation request is required"))
	}
	if err := validateMutationContext(ctx, request.GetContext(), s.limiter, s.authToken); err != nil {
		return nil, s.fail(ctx, request.GetContext(), "mutation.execute", request, err, started)
	}
	if strings.TrimSpace(request.GetApprovalToken()) == "" {
		return nil, s.fail(ctx, request.GetContext(), "mutation.execute", request, permissionDenied("approval token is required"), started)
	}
	if !idempotencyPattern.MatchString(request.GetIdempotencyKey()) {
		return nil, s.fail(ctx, request.GetContext(), "mutation.execute", request, invalid("idempotency_key must contain 8 to 128 safe characters"), started)
	}
	spec, call, err := s.mutationCall(ctx, request)
	if err != nil {
		return nil, s.fail(ctx, request.GetContext(), "mutation.execute", request, err, started)
	}
	actorID := request.GetContext().GetCaller().GetActorId()
	record, replayed, err := s.mutationAuthorizer.Authorize(
		ctx, request.GetApprovalToken(), request.GetIdempotencyKey(),
		request.GetContext().GetInvestigationId(), actorID, spec,
	)
	if err != nil {
		return nil, s.fail(ctx, request.GetContext(), spec.ToolName, request, err, started)
	}
	if replayed {
		s.writeAudit(ctx, request.GetContext(), spec.ToolName, request, "replayed", "", started)
		return mutationExecutionProto(record, true), nil
	}
	result, callErr := call()
	if callErr != nil {
		safeError := "approved Kubernetes mutation failed"
		var domain *Error
		if errors.As(callErr, &domain) {
			safeError = domain.Message
		}
		record, _ = s.mutationAuthorizer.Finalize(ctx, record.ExecutionID, "FAILED", nil, safeError)
		return nil, s.fail(ctx, request.GetContext(), spec.ToolName, request, callErr, started)
	}
	payload, _, err := sanitizePayload(result.Data)
	if err != nil {
		_, _ = s.mutationAuthorizer.Finalize(ctx, record.ExecutionID, "FAILED", nil, "mutation result could not be encoded")
		return nil, s.fail(ctx, request.GetContext(), spec.ToolName, request, err, started)
	}
	record, err = s.mutationAuthorizer.Finalize(ctx, record.ExecutionID, "SUCCEEDED", payload, "")
	if err != nil {
		return nil, s.fail(ctx, request.GetContext(), spec.ToolName, request, err, started)
	}
	s.writeAudit(ctx, request.GetContext(), spec.ToolName, request, "success", "", started)
	return mutationExecutionProto(record, false), nil
}

func (s *Server) GetMutationExecution(ctx context.Context, request *toolgatewayv1.GetMutationExecutionRequest) (*toolgatewayv1.MutationExecution, error) {
	started := time.Now()
	if request == nil {
		return nil, toStatus(invalid("execution request is required"))
	}
	if err := validateContext(ctx, request.GetContext(), s.limiter, s.authToken); err != nil {
		return nil, s.fail(ctx, request.GetContext(), "mutation.get_execution", request, err, started)
	}
	if !idempotencyPattern.MatchString(request.GetIdempotencyKey()) {
		return nil, s.fail(ctx, request.GetContext(), "mutation.get_execution", request, invalid("idempotency_key must contain 8 to 128 safe characters"), started)
	}
	record, err := s.mutationAuthorizer.Get(ctx, request.GetContext().GetInvestigationId(), request.GetIdempotencyKey())
	if err != nil {
		return nil, s.fail(ctx, request.GetContext(), "mutation.get_execution", request, err, started)
	}
	s.writeAudit(ctx, request.GetContext(), "mutation.get_execution", request, "success", "", started)
	return mutationExecutionProto(record, false), nil
}

func (s *Server) mutationCall(ctx context.Context, request *toolgatewayv1.ExecuteApprovedMutationRequest) (mutationSpec, func() (connectorResult, error), error) {
	var spec mutationSpec
	var call func() (connectorResult, error)
	switch operation := request.GetOperation().(type) {
	case *toolgatewayv1.ExecuteApprovedMutationRequest_RestartDeployment:
		args := operation.RestartDeployment
		if err := s.validateMutationTarget(args.GetNamespace(), args.GetName()); err != nil {
			return spec, nil, err
		}
		spec = newMutationSpec("kubernetes.restart_deployment", args.GetNamespace(), args.GetName(), map[string]any{"name": args.GetName(), "namespace": args.GetNamespace()})
		call = func() (connectorResult, error) {
			return s.kubernetes.RestartDeployment(ctx, args.GetNamespace(), args.GetName(), time.Now())
		}
	case *toolgatewayv1.ExecuteApprovedMutationRequest_ScaleDeployment:
		args := operation.ScaleDeployment
		if err := s.validateMutationTarget(args.GetNamespace(), args.GetName()); err != nil {
			return spec, nil, err
		}
		if args.GetReplicas() < 0 || args.GetReplicas() > maximumReplicas {
			return spec, nil, invalid("replicas must be between 0 and 100")
		}
		spec = newMutationSpec("kubernetes.scale_deployment", args.GetNamespace(), args.GetName(), map[string]any{"name": args.GetName(), "namespace": args.GetNamespace(), "replicas": args.GetReplicas()})
		call = func() (connectorResult, error) {
			return s.kubernetes.ScaleDeployment(ctx, args.GetNamespace(), args.GetName(), args.GetReplicas())
		}
	case *toolgatewayv1.ExecuteApprovedMutationRequest_RollbackDeployment:
		args := operation.RollbackDeployment
		if err := s.validateMutationTarget(args.GetNamespace(), args.GetName()); err != nil {
			return spec, nil, err
		}
		if args.GetRevision() < 0 {
			return spec, nil, invalid("revision must be zero or a positive integer")
		}
		spec = newMutationSpec("kubernetes.rollback_deployment", args.GetNamespace(), args.GetName(), map[string]any{"name": args.GetName(), "namespace": args.GetNamespace(), "revision": args.GetRevision()})
		call = func() (connectorResult, error) {
			return s.kubernetes.RollbackDeployment(ctx, args.GetNamespace(), args.GetName(), args.GetRevision())
		}
	default:
		return spec, nil, invalid("one typed mutation operation is required")
	}
	return spec, call, nil
}

func (s *Server) validateMutationTarget(namespace, name string) error {
	if !kubernetesNamePattern.MatchString(namespace) || !kubernetesNamePattern.MatchString(name) {
		return invalid("namespace and name must be valid Kubernetes names")
	}
	if namespace != s.allowedNamespace {
		return permissionDenied("mutation target is outside the isolated namespace")
	}
	return nil
}

func newMutationSpec(toolName, namespace, name string, parameters map[string]any) mutationSpec {
	hash, _ := canonicalHash(parameters)
	return mutationSpec{ToolName: toolName, Target: namespace + "/deployment/" + name, Parameters: parameters, ParametersHash: hash}
}

func mutationExecutionProto(record mutationExecution, replayed bool) *toolgatewayv1.MutationExecution {
	statuses := map[string]toolgatewayv1.MutationExecutionStatus{
		"EXECUTING": toolgatewayv1.MutationExecutionStatus_MUTATION_EXECUTION_STATUS_EXECUTING,
		"SUCCEEDED": toolgatewayv1.MutationExecutionStatus_MUTATION_EXECUTION_STATUS_SUCCEEDED,
		"FAILED":    toolgatewayv1.MutationExecutionStatus_MUTATION_EXECUTION_STATUS_FAILED,
	}
	response := &toolgatewayv1.MutationExecution{
		ExecutionId: record.ExecutionID, ApprovalId: record.ApprovalID,
		InvestigationId: record.InvestigationID, ToolName: record.ToolName,
		Target: record.Target, ParametersHash: record.ParametersHash,
		IdempotencyKey: record.IdempotencyKey, Status: statuses[record.Status],
		JsonPayload: record.Result, SafeError: record.SafeError, Replayed: replayed,
	}
	if !record.StartedAt.IsZero() {
		response.StartedAt = timestamppb.New(record.StartedAt)
	}
	if record.FinishedAt != nil {
		response.FinishedAt = timestamppb.New(*record.FinishedAt)
	}
	return response
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
