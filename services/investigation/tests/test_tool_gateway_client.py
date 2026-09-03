import asyncio
from typing import Any

import grpc

from ai_sre_investigation.generated import tool_gateway_pb2 as pb
from ai_sre_investigation.generated import tool_gateway_pb2_grpc as pb_grpc
from ai_sre_investigation.ports import MutationRequest, ToolRequest
from ai_sre_investigation.tool_gateway_client import GrpcToolClient, ToolGatewayError


class RecordingGateway(pb_grpc.ToolGatewayV1Servicer):
    def __init__(self) -> None:
        self.request: pb.QueryPrometheusRequest | None = None
        self.authorization = ""
        self.had_deadline = False
        self.mutations: list[pb.ExecuteApprovedMutationRequest] = []

    async def QueryPrometheus(
        self,
        request: pb.QueryPrometheusRequest,
        context: grpc.aio.ServicerContext[pb.QueryPrometheusRequest, pb.ReadToolResponse],
    ) -> pb.ReadToolResponse:
        self.request = request
        metadata: dict[str, str | bytes] = dict(context.invocation_metadata() or ())
        authorization = metadata.get("authorization", "")
        self.authorization = (
            authorization.decode() if isinstance(authorization, bytes) else authorization
        )
        self.had_deadline = context.time_remaining() is not None
        return pb.ReadToolResponse(
            tool_name="prometheus.query",
            source_ref="fake://prometheus",
            json_payload=b'{"status":"success"}',
        )

    async def QueryLoki(self, request: Any, context: Any) -> pb.ReadToolResponse:
        return _response("loki.query_range")

    async def GetTempoTrace(self, request: Any, context: Any) -> pb.ReadToolResponse:
        return _response("tempo.get_trace")

    async def SearchTempoTraces(self, request: Any, context: Any) -> pb.ReadToolResponse:
        return _response("tempo.search_traces")

    async def ListReleases(self, request: Any, context: Any) -> pb.ReadToolResponse:
        return _response("releases.list")

    async def GetGitCommit(self, request: Any, context: Any) -> pb.ReadToolResponse:
        return _response("git.get_commit")

    async def GetKubernetesWorkload(self, request: Any, context: Any) -> pb.ReadToolResponse:
        return _response("kubernetes.get_workload")

    async def ListKubernetesEvents(self, request: Any, context: Any) -> pb.ReadToolResponse:
        return _response("kubernetes.list_events")

    async def ExecuteApprovedMutation(
        self, request: pb.ExecuteApprovedMutationRequest, context: Any
    ) -> pb.MutationExecution:
        del context
        self.mutations.append(request)
        return pb.MutationExecution(
            execution_id=f"exec-{len(self.mutations)}",
            approval_id="apr-1",
            investigation_id=request.context.investigation_id,
            tool_name={
                "restart_deployment": "kubernetes.restart_deployment",
                "scale_deployment": "kubernetes.scale_deployment",
                "rollback_deployment": "kubernetes.rollback_deployment",
            }[request.WhichOneof("operation")],
            target="ai-sre-test/deployment/payment",
            parameters_hash="a" * 64,
            idempotency_key=request.idempotency_key,
            status=pb.MUTATION_EXECUTION_STATUS_SUCCEEDED,
            json_payload=b'{"changed":true}',
        )

    async def GetMutationExecution(
        self, request: pb.GetMutationExecutionRequest, context: Any
    ) -> pb.MutationExecution:
        del context
        return pb.MutationExecution(
            execution_id="exec-existing",
            approval_id="apr-1",
            investigation_id=request.context.investigation_id,
            tool_name="kubernetes.scale_deployment",
            target="ai-sre-test/deployment/payment",
            parameters_hash="a" * 64,
            idempotency_key=request.idempotency_key,
            status=pb.MUTATION_EXECUTION_STATUS_EXECUTING,
        )


def _response(tool_name: str) -> pb.ReadToolResponse:
    return pb.ReadToolResponse(
        tool_name=tool_name, source_ref=f"fake://{tool_name}", json_payload=b'{"ok":true}'
    )


def test_generated_client_propagates_identity_trace_auth_and_deadline() -> None:
    async def scenario() -> None:
        server = grpc.aio.server()
        servicer = RecordingGateway()
        pb_grpc.add_ToolGatewayV1Servicer_to_server(servicer, server)  # type: ignore[no-untyped-call]
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        client = GrpcToolClient(
            f"127.0.0.1:{port}",
            "test-token",
            actor_id="investigator-1",
            timeout_seconds=1,
        )
        try:
            response = await client.execute_read(
                ToolRequest(
                    investigation_id="inv-1",
                    trace_id="0123456789abcdef",
                    tool_name="prometheus.query",
                    arguments={"promql": "up"},
                )
            )
        finally:
            await client.close()
            await server.stop(grace=0)

        assert response.data == {"status": "success"}
        assert response.tool_name == "prometheus.query"
        assert servicer.request is not None
        assert servicer.request.context.trace_id == "0123456789abcdef"
        assert servicer.request.context.caller.actor_id == "investigator-1"
        assert servicer.authorization == "Bearer test-token"
        assert servicer.had_deadline

    asyncio.run(scenario())


def test_generated_client_rejects_dynamic_tool_name() -> None:
    async def scenario() -> None:
        client = GrpcToolClient("127.0.0.1:1", "test-token", actor_id="investigator-1")
        try:
            request = ToolRequest(
                investigation_id="inv-1",
                trace_id="0123456789abcdef",
                tool_name="shell.exec",
                arguments={"command": "id"},
            )
            try:
                await client.execute_read(request)
            except LookupError as error:
                assert "unregistered read tool" in str(error)
            else:
                raise AssertionError("dynamic tool name was accepted")
        finally:
            await client.close()

    asyncio.run(scenario())


def test_generated_client_dispatches_remaining_fixed_tools() -> None:
    async def scenario() -> None:
        server = grpc.aio.server()
        pb_grpc.add_ToolGatewayV1Servicer_to_server(  # type: ignore[no-untyped-call]
            RecordingGateway(), server
        )
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        client = GrpcToolClient(f"127.0.0.1:{port}", "token", actor_id="actor")
        common = {
            "investigation_id": "inv-1",
            "trace_id": "0123456789abcdef",
        }
        requests = [
            ToolRequest(
                **common,
                tool_name="loki.query_range",
                arguments={
                    "logql": '{service="payment"}',
                    "start": "2026-09-02T00:00:00Z",
                    "end": "2026-09-02T01:00:00Z",
                },
            ),
            ToolRequest(
                **common,
                tool_name="tempo.get_trace",
                arguments={"trace_id": "0123456789abcdef0123456789abcdef"},
            ),
            ToolRequest(
                **common,
                tool_name="tempo.search_traces",
                arguments={
                    "traceql": "{ true }",
                    "start": "2026-09-02T00:00:00Z",
                    "end": "2026-09-02T01:00:00Z",
                },
            ),
            ToolRequest(
                **common,
                tool_name="releases.list",
                arguments={
                    "service": "payment",
                    "start": "2026-09-02T00:00:00Z",
                    "end": "2026-09-02T01:00:00Z",
                },
            ),
            ToolRequest(
                **common,
                tool_name="git.get_commit",
                arguments={"revision": "HEAD", "max_changed_files": 10},
            ),
            ToolRequest(
                **common,
                tool_name="kubernetes.get_workload",
                arguments={"namespace": "default", "kind": "Deployment", "name": "payment"},
            ),
            ToolRequest(
                **common,
                tool_name="kubernetes.list_events",
                arguments={"namespace": "default", "limit": 10},
            ),
        ]
        try:
            responses = [await client.execute_read(request) for request in requests]
        finally:
            await client.close()
            await server.stop(grace=0)
        assert [response.tool_name for response in responses] == [
            request.tool_name for request in requests
        ]

    asyncio.run(scenario())


def test_generated_client_dispatches_typed_mutations_and_execution_query() -> None:
    async def scenario() -> None:
        server = grpc.aio.server()
        gateway = RecordingGateway()
        pb_grpc.add_ToolGatewayV1Servicer_to_server(gateway, server)  # type: ignore[no-untyped-call]
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        client = GrpcToolClient(f"127.0.0.1:{port}", "token", actor_id="service")
        common = {
            "investigation_id": "inv-1",
            "trace_id": "0123456789abcdef",
            "actor_id": "approver-1",
            "approval_token": "approval-token",
        }
        requests = [
            MutationRequest(
                **common,
                idempotency_key="idem-restart-1",
                tool_name="kubernetes.restart_deployment",
                parameters={"namespace": "ai-sre-test", "name": "payment"},
            ),
            MutationRequest(
                **common,
                idempotency_key="idem-scale-001",
                tool_name="kubernetes.scale_deployment",
                parameters={"namespace": "ai-sre-test", "name": "payment", "replicas": 3},
            ),
            MutationRequest(
                **common,
                idempotency_key="idem-rollback1",
                tool_name="kubernetes.rollback_deployment",
                parameters={"namespace": "ai-sre-test", "name": "payment", "revision": 1},
            ),
        ]
        try:
            responses = [await client.execute_mutation(request) for request in requests]
            existing = await client.get_mutation_execution(
                "inv-1", "0123456789abcdef", "idem-scale-001"
            )
        finally:
            await client.close()
            await server.stop(grace=0)
        assert [item.status for item in responses] == ["SUCCEEDED"] * 3
        assert [item.WhichOneof("operation") for item in gateway.mutations] == [
            "restart_deployment",
            "scale_deployment",
            "rollback_deployment",
        ]
        assert all(item.context.caller.role == "approver" for item in gateway.mutations)
        assert existing.status == "EXECUTING"

    asyncio.run(scenario())


def test_client_configuration_and_fallback_error_are_safe() -> None:
    for kwargs in (
        {"auth_token": "", "actor_id": "actor"},
        {"auth_token": "token", "actor_id": "actor", "role": "viewer"},
        {"auth_token": "token", "actor_id": "actor", "timeout_seconds": 0},
    ):
        try:
            GrpcToolClient("127.0.0.1:1", **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid client configuration was accepted")

    async def scenario() -> None:
        client = GrpcToolClient("127.0.0.1:1", "token", actor_id="actor", timeout_seconds=0.01)
        try:
            await client.execute_read(
                ToolRequest(
                    investigation_id="inv-1",
                    trace_id="0123456789abcdef",
                    tool_name="prometheus.query",
                    arguments={"promql": "up"},
                )
            )
        except ToolGatewayError as error:
            assert error.code in {"UNAVAILABLE", "DEADLINE_EXCEEDED"}
            assert not error.retryable
        else:
            raise AssertionError("unreachable gateway call succeeded")
        finally:
            await client.close()

    asyncio.run(scenario())
