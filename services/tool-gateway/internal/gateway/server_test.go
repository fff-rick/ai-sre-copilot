package gateway

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	toolgatewayv1 "ai-sre-copilot.local/tool-gateway/gen/ai/sre/toolgateway/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/test/bufconn"
	"google.golang.org/protobuf/types/known/timestamppb"
)

type fakeSources struct {
	result connectorResult
	err    error
	block  bool
	calls  atomic.Int64
}

func (f *fakeSources) response(ctx context.Context, source string) (connectorResult, error) {
	f.calls.Add(1)
	if f.block {
		<-ctx.Done()
		return connectorResult{}, ctx.Err()
	}
	if f.err != nil {
		return connectorResult{}, f.err
	}
	result := f.result
	if result.SourceRef == "" {
		result.SourceRef = source
	}
	return result, nil
}

func (f *fakeSources) QueryPrometheus(ctx context.Context, _ string, _ time.Time) (connectorResult, error) {
	return f.response(ctx, "fake://prometheus")
}
func (f *fakeSources) QueryLoki(ctx context.Context, _ string, _, _ time.Time, _ uint32, _ string) (connectorResult, error) {
	return f.response(ctx, "fake://loki")
}
func (f *fakeSources) GetTempoTrace(ctx context.Context, _ string) (connectorResult, error) {
	return f.response(ctx, "fake://tempo/trace")
}
func (f *fakeSources) SearchTempoTraces(ctx context.Context, _ string, _, _ time.Time, _ uint32) (connectorResult, error) {
	return f.response(ctx, "fake://tempo/search")
}
func (f *fakeSources) ListReleases(ctx context.Context, _ string, _, _ time.Time, _ uint32) (connectorResult, error) {
	return f.response(ctx, "fake://releases")
}
func (f *fakeSources) GetCommit(ctx context.Context, _ string, _ uint32) (connectorResult, error) {
	return f.response(ctx, "fake://git")
}
func (f *fakeSources) GetWorkload(ctx context.Context, _, _, _ string) (connectorResult, error) {
	return f.response(ctx, "fake://kubernetes/workload")
}
func (f *fakeSources) ListEvents(ctx context.Context, _, _, _ string, _ uint32) (connectorResult, error) {
	return f.response(ctx, "fake://kubernetes/events")
}
func (f *fakeSources) RestartDeployment(ctx context.Context, _, _ string, _ time.Time) (connectorResult, error) {
	return f.response(ctx, "fake://kubernetes/restart")
}
func (f *fakeSources) ScaleDeployment(ctx context.Context, _, _ string, _ int32) (connectorResult, error) {
	return f.response(ctx, "fake://kubernetes/scale")
}
func (f *fakeSources) RollbackDeployment(ctx context.Context, _, _ string, _ int64) (connectorResult, error) {
	return f.response(ctx, "fake://kubernetes/rollback")
}

type memoryAuditSink struct {
	mu     sync.Mutex
	events []auditEvent
}

type memoryMutationAuthorizer struct {
	token      string
	want       mutationSpec
	records    map[string]mutationExecution
	authorized atomic.Int64
}

func (a *memoryMutationAuthorizer) Authorize(_ context.Context, token, key, investigationID, _ string, spec mutationSpec) (mutationExecution, bool, error) {
	if token != a.token {
		return mutationExecution{}, false, permissionDenied("approval token is invalid")
	}
	if spec.ToolName != a.want.ToolName || spec.Target != a.want.Target || spec.ParametersHash != a.want.ParametersHash {
		return mutationExecution{}, false, permissionDenied("approval token does not match mutation parameters")
	}
	if record, ok := a.records[key]; ok {
		return record, true, nil
	}
	a.authorized.Add(1)
	record := mutationExecution{ExecutionID: "exec-test", ApprovalID: "apr-test", InvestigationID: investigationID, ToolName: spec.ToolName, Target: spec.Target, ParametersHash: spec.ParametersHash, IdempotencyKey: key, Status: "EXECUTING", StartedAt: time.Now()}
	a.records[key] = record
	return record, false, nil
}

func (a *memoryMutationAuthorizer) Finalize(_ context.Context, executionID, executionStatus string, result []byte, safeError string) (mutationExecution, error) {
	for key, record := range a.records {
		if record.ExecutionID == executionID {
			now := time.Now()
			record.Status, record.Result, record.SafeError, record.FinishedAt = executionStatus, result, safeError, &now
			a.records[key] = record
			return record, nil
		}
	}
	return mutationExecution{}, notFound("mutation execution was not found")
}

func (a *memoryMutationAuthorizer) Get(_ context.Context, investigationID, key string) (mutationExecution, error) {
	record, ok := a.records[key]
	if !ok || record.InvestigationID != investigationID {
		return mutationExecution{}, notFound("mutation execution was not found")
	}
	return record, nil
}

func (s *memoryAuditSink) Write(_ context.Context, event auditEvent) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.events = append(s.events, event)
}

func testClient(t *testing.T, sources *fakeSources, requestsPerSecond float64, burst int) (toolgatewayv1.ToolGatewayV1Client, *memoryAuditSink) {
	t.Helper()
	audit := &memoryAuditSink{}
	artifacts, err := newArtifactStore(t.TempDir(), 1024*1024)
	if err != nil {
		t.Fatal(err)
	}
	server, err := newServer(serverOptions{
		Observability: sources, Releases: sources, Git: sources, Kubernetes: sources,
		Limiter: newActorLimiter(requestsPerSecond, burst), Audit: audit, Artifacts: artifacts,
		InlineBytes: 1024, AuthToken: "test-token",
	})
	if err != nil {
		t.Fatal(err)
	}
	listener := bufconn.Listen(1024 * 1024)
	grpcServer := grpc.NewServer()
	toolgatewayv1.RegisterToolGatewayV1Server(grpcServer, server)
	go func() { _ = grpcServer.Serve(listener) }()
	t.Cleanup(grpcServer.Stop)
	connection, err := grpc.NewClient("passthrough:///bufnet",
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithContextDialer(func(context.Context, string) (net.Conn, error) { return listener.Dial() }),
	)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = connection.Close() })
	return toolgatewayv1.NewToolGatewayV1Client(connection), audit
}

func mutationTestClient(t *testing.T, sources *fakeSources, authorizer mutationAuthorizer) toolgatewayv1.ToolGatewayV1Client {
	t.Helper()
	audit := &memoryAuditSink{}
	artifacts, err := newArtifactStore(t.TempDir(), 1024*1024)
	if err != nil {
		t.Fatal(err)
	}
	server, err := newServer(serverOptions{
		Observability: sources, Releases: sources, Git: sources, Kubernetes: sources,
		Limiter: newActorLimiter(1000, 100), Audit: audit, Artifacts: artifacts,
		InlineBytes: 1024, AuthToken: "test-token", MutationAuthorizer: authorizer,
		AllowedNamespace: "ai-sre-test",
	})
	if err != nil {
		t.Fatal(err)
	}
	listener := bufconn.Listen(1024 * 1024)
	grpcServer := grpc.NewServer()
	toolgatewayv1.RegisterToolGatewayV1Server(grpcServer, server)
	go func() { _ = grpcServer.Serve(listener) }()
	t.Cleanup(grpcServer.Stop)
	connection, err := grpc.NewClient("passthrough:///bufnet",
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithContextDialer(func(context.Context, string) (net.Conn, error) { return listener.Dial() }),
	)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = connection.Close() })
	return toolgatewayv1.NewToolGatewayV1Client(connection)
}

func rpcContext(t *testing.T, role string, timeout time.Duration) (context.Context, context.CancelFunc) {
	t.Helper()
	ctx := metadata.AppendToOutgoingContext(context.Background(), "authorization", "Bearer test-token")
	return context.WithTimeout(ctx, timeout)
}

func requestContext(role string) *toolgatewayv1.RequestContext {
	return &toolgatewayv1.RequestContext{InvestigationId: "inv-contract", TraceId: "0123456789abcdef", Caller: &toolgatewayv1.CallerIdentity{ActorId: "test-actor", Role: role}}
}

func TestEightReadToolContracts(t *testing.T) {
	sources := &fakeSources{result: connectorResult{Data: map[string]any{"status": "ok"}}}
	client, audit := testClient(t, sources, 1000, 100)
	start := timestamppb.New(time.Now().Add(-time.Hour))
	end := timestamppb.Now()
	tests := []struct {
		name string
		call func(context.Context) (*toolgatewayv1.ReadToolResponse, error)
	}{
		{"prometheus.query", func(ctx context.Context) (*toolgatewayv1.ReadToolResponse, error) {
			return client.QueryPrometheus(ctx, &toolgatewayv1.QueryPrometheusRequest{Context: requestContext("investigator"), Args: &toolgatewayv1.QueryPrometheusArgs{Promql: "up"}})
		}},
		{"loki.query_range", func(ctx context.Context) (*toolgatewayv1.ReadToolResponse, error) {
			return client.QueryLoki(ctx, &toolgatewayv1.QueryLokiRequest{Context: requestContext("investigator"), Args: &toolgatewayv1.QueryLokiArgs{Logql: `{service="payment"}`, Start: start, End: end, Limit: 10}})
		}},
		{"tempo.get_trace", func(ctx context.Context) (*toolgatewayv1.ReadToolResponse, error) {
			return client.GetTempoTrace(ctx, &toolgatewayv1.GetTempoTraceRequest{Context: requestContext("investigator"), Args: &toolgatewayv1.GetTempoTraceArgs{TraceId: "0123456789abcdef0123456789abcdef"}})
		}},
		{"tempo.search_traces", func(ctx context.Context) (*toolgatewayv1.ReadToolResponse, error) {
			return client.SearchTempoTraces(ctx, &toolgatewayv1.SearchTempoTracesRequest{Context: requestContext("investigator"), Args: &toolgatewayv1.SearchTempoTracesArgs{Traceql: `{ resource.service.name = "payment" }`, Start: start, End: end, Limit: 10}})
		}},
		{"releases.list", func(ctx context.Context) (*toolgatewayv1.ReadToolResponse, error) {
			return client.ListReleases(ctx, &toolgatewayv1.ListReleasesRequest{Context: requestContext("investigator"), Args: &toolgatewayv1.ListReleasesArgs{Service: "payment", Start: start, End: end, Limit: 10}})
		}},
		{"git.get_commit", func(ctx context.Context) (*toolgatewayv1.ReadToolResponse, error) {
			return client.GetGitCommit(ctx, &toolgatewayv1.GetGitCommitRequest{Context: requestContext("investigator"), Args: &toolgatewayv1.GetGitCommitArgs{Revision: "HEAD", MaxChangedFiles: 10}})
		}},
		{"kubernetes.get_workload", func(ctx context.Context) (*toolgatewayv1.ReadToolResponse, error) {
			return client.GetKubernetesWorkload(ctx, &toolgatewayv1.GetKubernetesWorkloadRequest{Context: requestContext("investigator"), Args: &toolgatewayv1.GetKubernetesWorkloadArgs{Namespace: "default", Kind: "Deployment", Name: "payment"}})
		}},
		{"kubernetes.list_events", func(ctx context.Context) (*toolgatewayv1.ReadToolResponse, error) {
			return client.ListKubernetesEvents(ctx, &toolgatewayv1.ListKubernetesEventsRequest{Context: requestContext("investigator"), Args: &toolgatewayv1.ListKubernetesEventsArgs{Namespace: "default", InvolvedObjectKind: "Pod", InvolvedObjectName: "payment-0", Limit: 10}})
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			ctx, cancel := rpcContext(t, "investigator", time.Second)
			defer cancel()
			response, err := test.call(ctx)
			if err != nil {
				t.Fatalf("RPC failed: %v", err)
			}
			if response.GetToolName() != test.name || len(response.GetJsonPayload()) == 0 {
				t.Fatalf("unexpected response: %+v", response)
			}
		})
	}
	if sources.calls.Load() != 8 {
		t.Fatalf("connector calls = %d, want 8", sources.calls.Load())
	}
	if len(audit.events) != 8 {
		t.Fatalf("audit events = %d, want 8", len(audit.events))
	}
}

func TestStableErrors(t *testing.T) {
	t.Run("authentication", func(t *testing.T) {
		client, _ := testClient(t, &fakeSources{}, 100, 10)
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		_, err := client.QueryPrometheus(ctx, &toolgatewayv1.QueryPrometheusRequest{Context: requestContext("investigator"), Args: &toolgatewayv1.QueryPrometheusArgs{Promql: "up"}})
		assertToolError(t, err, codes.Unauthenticated, toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_UNAUTHENTICATED)
	})
	t.Run("missing deadline", func(t *testing.T) {
		client, _ := testClient(t, &fakeSources{}, 100, 10)
		ctx := metadata.AppendToOutgoingContext(context.Background(), "authorization", "Bearer test-token")
		_, err := client.QueryPrometheus(ctx, &toolgatewayv1.QueryPrometheusRequest{Context: requestContext("investigator"), Args: &toolgatewayv1.QueryPrometheusArgs{Promql: "up"}})
		assertToolError(t, err, codes.InvalidArgument, toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_INVALID_ARGUMENT)
	})
	t.Run("permission", func(t *testing.T) {
		client, _ := testClient(t, &fakeSources{}, 100, 10)
		ctx, cancel := rpcContext(t, "viewer", time.Second)
		defer cancel()
		_, err := client.QueryPrometheus(ctx, &toolgatewayv1.QueryPrometheusRequest{Context: requestContext("viewer"), Args: &toolgatewayv1.QueryPrometheusArgs{Promql: "up"}})
		assertToolError(t, err, codes.PermissionDenied, toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_PERMISSION_DENIED)
	})
	t.Run("rate limit", func(t *testing.T) {
		client, _ := testClient(t, &fakeSources{result: connectorResult{Data: map[string]any{"ok": true}}}, 0.001, 1)
		for index := 0; index < 2; index++ {
			ctx, cancel := rpcContext(t, "investigator", time.Second)
			_, err := client.QueryPrometheus(ctx, &toolgatewayv1.QueryPrometheusRequest{Context: requestContext("investigator"), Args: &toolgatewayv1.QueryPrometheusArgs{Promql: "up"}})
			cancel()
			if index == 1 {
				assertToolError(t, err, codes.ResourceExhausted, toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_RATE_LIMITED)
			}
		}
	})
	t.Run("source unavailable", func(t *testing.T) {
		client, _ := testClient(t, &fakeSources{err: sourceUnavailable(errors.New("contains upstream secret"))}, 100, 10)
		ctx, cancel := rpcContext(t, "investigator", time.Second)
		defer cancel()
		_, err := client.QueryPrometheus(ctx, &toolgatewayv1.QueryPrometheusRequest{Context: requestContext("investigator"), Args: &toolgatewayv1.QueryPrometheusArgs{Promql: "up"}})
		assertToolError(t, err, codes.Unavailable, toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_SOURCE_UNAVAILABLE)
		if status.Convert(err).Message() != "data source is unavailable" {
			t.Fatalf("unsafe error message: %v", err)
		}
	})
	t.Run("deadline", func(t *testing.T) {
		client, _ := testClient(t, &fakeSources{block: true}, 100, 10)
		ctx, cancel := rpcContext(t, "investigator", 20*time.Millisecond)
		defer cancel()
		_, err := client.QueryPrometheus(ctx, &toolgatewayv1.QueryPrometheusRequest{Context: requestContext("investigator"), Args: &toolgatewayv1.QueryPrometheusArgs{Promql: "up"}})
		if status.Code(err) != codes.DeadlineExceeded {
			t.Fatalf("status = %v, want deadline exceeded", status.Code(err))
		}
	})
}

func TestRedactsSecretsBeforeReturning(t *testing.T) {
	client, _ := testClient(t, &fakeSources{result: connectorResult{Data: map[string]any{"authorization": "Bearer abc", "message": "failed with Bearer xyz"}}}, 100, 10)
	ctx, cancel := rpcContext(t, "investigator", time.Second)
	defer cancel()
	response, err := client.QueryPrometheus(ctx, &toolgatewayv1.QueryPrometheusRequest{Context: requestContext("investigator"), Args: &toolgatewayv1.QueryPrometheusArgs{Promql: "up"}})
	if err != nil {
		t.Fatal(err)
	}
	var payload map[string]any
	if err := json.Unmarshal(response.GetJsonPayload(), &payload); err != nil {
		t.Fatal(err)
	}
	if !response.GetRedacted() || payload["authorization"] != "[REDACTED]" || payload["message"] != "failed with Bearer [REDACTED]" {
		t.Fatalf("payload was not redacted: %s", response.GetJsonPayload())
	}
}

func TestLargeResponseBecomesArtifactAndHardLimitIsStable(t *testing.T) {
	large := make([]byte, 2048)
	client, _ := testClient(t, &fakeSources{result: connectorResult{Data: map[string]any{"data": string(large)}}}, 100, 10)
	ctx, cancel := rpcContext(t, "investigator", time.Second)
	response, err := client.QueryPrometheus(ctx, &toolgatewayv1.QueryPrometheusRequest{Context: requestContext("investigator"), Args: &toolgatewayv1.QueryPrometheusArgs{Promql: "up"}})
	cancel()
	if err != nil {
		t.Fatal(err)
	}
	if response.GetArtifact().GetUri() == "" || len(response.GetArtifact().GetSha256()) != 64 || len(response.GetJsonPayload()) != 0 {
		t.Fatalf("unexpected artifact response: %+v", response)
	}

	tooLarge := make([]byte, 1024*1024+1)
	client, _ = testClient(t, &fakeSources{result: connectorResult{Data: map[string]any{"data": string(tooLarge)}}}, 100, 10)
	ctx, cancel = rpcContext(t, "investigator", 5*time.Second)
	_, err = client.QueryPrometheus(ctx, &toolgatewayv1.QueryPrometheusRequest{Context: requestContext("investigator"), Args: &toolgatewayv1.QueryPrometheusArgs{Promql: "up"}})
	cancel()
	assertToolError(t, err, codes.ResourceExhausted, toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_RESPONSE_TOO_LARGE)
}

func TestConcurrentCalls(t *testing.T) {
	sources := &fakeSources{result: connectorResult{Data: map[string]any{"ok": true}}}
	client, _ := testClient(t, sources, 10000, 200)
	var group sync.WaitGroup
	for index := 0; index < 64; index++ {
		group.Add(1)
		go func() {
			defer group.Done()
			ctx, cancel := rpcContext(t, "investigator", time.Second)
			defer cancel()
			_, err := client.QueryPrometheus(ctx, &toolgatewayv1.QueryPrometheusRequest{Context: requestContext("investigator"), Args: &toolgatewayv1.QueryPrometheusArgs{Promql: "up"}})
			if err != nil {
				t.Errorf("concurrent RPC failed: %v", err)
			}
		}()
	}
	group.Wait()
	if sources.calls.Load() != 64 {
		t.Fatalf("connector calls = %d, want 64", sources.calls.Load())
	}
}

func TestApprovedMutationIsTypedBoundAndIdempotent(t *testing.T) {
	sources := &fakeSources{result: connectorResult{Data: map[string]any{"changed": true}}}
	want := newMutationSpec("kubernetes.scale_deployment", "ai-sre-test", "payment", map[string]any{"name": "payment", "namespace": "ai-sre-test", "replicas": int32(3)})
	authorizer := &memoryMutationAuthorizer{token: "approval-secret", want: want, records: make(map[string]mutationExecution)}
	client := mutationTestClient(t, sources, authorizer)
	call := func(role, token, key string, replicas int32) (*toolgatewayv1.MutationExecution, error) {
		ctx, cancel := rpcContext(t, role, time.Second)
		defer cancel()
		return client.ExecuteApprovedMutation(ctx, &toolgatewayv1.ExecuteApprovedMutationRequest{
			Context: requestContext(role), ApprovalToken: token, IdempotencyKey: key,
			Operation: &toolgatewayv1.ExecuteApprovedMutationRequest_ScaleDeployment{ScaleDeployment: &toolgatewayv1.ScaleDeploymentArgs{Namespace: "ai-sre-test", Name: "payment", Replicas: replicas}},
		})
	}
	first, err := call("approver", "approval-secret", "idem-stage5-001", 3)
	if err != nil || first.GetStatus() != toolgatewayv1.MutationExecutionStatus_MUTATION_EXECUTION_STATUS_SUCCEEDED || first.GetReplayed() {
		t.Fatalf("first mutation failed: response=%+v error=%v", first, err)
	}
	replay, err := call("approver", "approval-secret", "idem-stage5-001", 3)
	if err != nil || !replay.GetReplayed() || replay.GetExecutionId() != first.GetExecutionId() {
		t.Fatalf("idempotent replay failed: response=%+v error=%v", replay, err)
	}
	if sources.calls.Load() != 1 || authorizer.authorized.Load() != 1 {
		t.Fatalf("side effects=%d authorizations=%d, want one", sources.calls.Load(), authorizer.authorized.Load())
	}
	_, err = call("approver", "approval-secret", "idem-stage5-002", 10)
	assertToolError(t, err, codes.PermissionDenied, toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_PERMISSION_DENIED)
	_, err = call("investigator", "approval-secret", "idem-stage5-003", 3)
	assertToolError(t, err, codes.PermissionDenied, toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_PERMISSION_DENIED)
	_, err = call("approver", "", "idem-stage5-004", 3)
	assertToolError(t, err, codes.PermissionDenied, toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_PERMISSION_DENIED)
}

func TestMutationRejectsOutOfScopeAndUntypedRequests(t *testing.T) {
	authorizer := &memoryMutationAuthorizer{token: "token", records: make(map[string]mutationExecution)}
	client := mutationTestClient(t, &fakeSources{}, authorizer)
	ctx, cancel := rpcContext(t, "approver", time.Second)
	defer cancel()
	_, err := client.ExecuteApprovedMutation(ctx, &toolgatewayv1.ExecuteApprovedMutationRequest{
		Context: requestContext("approver"), ApprovalToken: "token", IdempotencyKey: "idem-stage5-101",
		Operation: &toolgatewayv1.ExecuteApprovedMutationRequest_RestartDeployment{RestartDeployment: &toolgatewayv1.RestartDeploymentArgs{Namespace: "production", Name: "payment"}},
	})
	assertToolError(t, err, codes.PermissionDenied, toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_PERMISSION_DENIED)
	ctx2, cancel2 := rpcContext(t, "approver", time.Second)
	defer cancel2()
	_, err = client.ExecuteApprovedMutation(ctx2, &toolgatewayv1.ExecuteApprovedMutationRequest{Context: requestContext("approver"), ApprovalToken: "token", IdempotencyKey: "idem-stage5-102"})
	assertToolError(t, err, codes.InvalidArgument, toolgatewayv1.ToolErrorCode_TOOL_ERROR_CODE_INVALID_ARGUMENT)
}

func assertToolError(t *testing.T, err error, wantStatus codes.Code, wantToolCode toolgatewayv1.ToolErrorCode) {
	t.Helper()
	if status.Code(err) != wantStatus {
		t.Fatalf("status = %v, want %v: %v", status.Code(err), wantStatus, err)
	}
	for _, detail := range status.Convert(err).Details() {
		if toolError, ok := detail.(*toolgatewayv1.ToolError); ok && toolError.GetCode() == wantToolCode {
			return
		}
	}
	t.Fatalf("missing ToolError detail %s: %v", wantToolCode, err)
}
