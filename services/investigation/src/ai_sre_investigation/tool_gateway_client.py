"""Generated gRPC adapter for the trusted Go tool gateway."""

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, NoReturn, cast

import grpc
from google.protobuf.timestamp_pb2 import Timestamp
from grpc_status import rpc_status
from pydantic import BaseModel, ConfigDict, Field

from ai_sre_investigation.generated import tool_gateway_pb2 as pb
from ai_sre_investigation.generated import tool_gateway_pb2_grpc as pb_grpc
from ai_sre_investigation.ports import (
    ArtifactReference,
    MutationRequest,
    MutationResponse,
    ToolRequest,
    ToolResponse,
)


class ToolGatewayError(RuntimeError):
    """Stable, caller-safe error returned by the tool gateway."""

    def __init__(self, code: str, safe_message: str, retryable: bool) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _PrometheusArgs(_Args):
    promql: str = Field(min_length=1, max_length=4000)
    at: datetime | None = None


class _LokiArgs(_Args):
    logql: str = Field(min_length=1, max_length=4000)
    start: datetime
    end: datetime
    limit: int = Field(default=100, ge=1, le=1000)
    direction: str = "backward"


class _TempoTraceArgs(_Args):
    trace_id: str = Field(pattern=r"^[a-fA-F0-9]{16,64}$")


class _TempoSearchArgs(_Args):
    traceql: str = Field(min_length=1, max_length=4000)
    start: datetime
    end: datetime
    limit: int = Field(default=100, ge=1, le=1000)


class _ReleaseArgs(_Args):
    service: str = Field(min_length=1, max_length=253)
    start: datetime
    end: datetime
    limit: int = Field(default=100, ge=1, le=1000)


class _GitArgs(_Args):
    revision: str = Field(min_length=1, max_length=200)
    max_changed_files: int = Field(default=100, ge=1, le=1000)


class _WorkloadArgs(_Args):
    namespace: str = Field(min_length=1, max_length=253)
    kind: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=253)


class _EventArgs(_Args):
    namespace: str = Field(min_length=1, max_length=253)
    involved_object_kind: str = ""
    involved_object_name: str = ""
    limit: int = Field(default=100, ge=1, le=1000)


class GrpcToolClient:
    """Whitelist dispatcher backed exclusively by generated V1 RPC methods."""

    def __init__(
        self,
        target: str,
        auth_token: str,
        *,
        actor_id: str,
        role: str = "investigator",
        timeout_seconds: float = 10.0,
    ) -> None:
        if not auth_token:
            raise ValueError("auth_token is required")
        if role not in {"investigator", "admin"}:
            raise ValueError("role must be investigator or admin")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._channel = grpc.aio.insecure_channel(target)
        self._stub = pb_grpc.ToolGatewayV1Stub(self._channel)  # type: ignore[no-untyped-call]
        self._metadata = (("authorization", f"Bearer {auth_token}"),)
        self._actor_id = actor_id
        self._role = role
        self._timeout_seconds = timeout_seconds

    async def close(self) -> None:
        """Close the underlying channel."""

        await self._channel.close()

    async def execute_read(self, request: ToolRequest) -> ToolResponse:
        """Validate arguments and dispatch only one of the eight fixed tools."""

        context = pb.RequestContext(
            investigation_id=request.investigation_id,
            trace_id=request.trace_id,
            caller=pb.CallerIdentity(actor_id=self._actor_id, role=self._role),
        )
        try:
            response = await self._dispatch(context, request.tool_name, request.arguments)
        except grpc.aio.AioRpcError as error:
            self._raise_stable(error)
        return self._decode(response)

    async def execute_mutation(self, request: MutationRequest) -> MutationResponse:
        """Dispatch one typed mutation; arbitrary commands cannot cross this adapter."""

        context = pb.RequestContext(
            investigation_id=request.investigation_id,
            trace_id=request.trace_id,
            caller=pb.CallerIdentity(actor_id=request.actor_id, role="approver"),
        )
        parameters = dict(request.parameters)
        namespace = str(parameters.get("namespace", ""))
        name = str(parameters.get("name", ""))
        kwargs: dict[str, Any] = {
            "context": context,
            "approval_token": request.approval_token,
            "idempotency_key": request.idempotency_key,
        }
        if request.tool_name == "kubernetes.restart_deployment":
            kwargs["restart_deployment"] = pb.RestartDeploymentArgs(namespace=namespace, name=name)
        elif request.tool_name == "kubernetes.scale_deployment":
            kwargs["scale_deployment"] = pb.ScaleDeploymentArgs(
                namespace=namespace, name=name, replicas=int(parameters.get("replicas", -1))
            )
        elif request.tool_name == "kubernetes.rollback_deployment":
            kwargs["rollback_deployment"] = pb.RollbackDeploymentArgs(
                namespace=namespace, name=name, revision=int(parameters.get("revision", -1))
            )
        else:
            raise LookupError(f"unregistered mutation tool: {request.tool_name}")
        try:
            response = await self._stub.ExecuteApprovedMutation(
                pb.ExecuteApprovedMutationRequest(**kwargs),
                timeout=self._timeout_seconds,
                metadata=self._metadata,
            )
        except grpc.aio.AioRpcError as error:
            self._raise_stable(error)
        return self._decode_mutation(response)

    async def get_mutation_execution(
        self, investigation_id: str, trace_id: str, idempotency_key: str
    ) -> MutationResponse:
        """Query durable execution state before deciding whether a retry is safe."""

        context = pb.RequestContext(
            investigation_id=investigation_id,
            trace_id=trace_id,
            caller=pb.CallerIdentity(actor_id=self._actor_id, role=self._role),
        )
        try:
            response = await self._stub.GetMutationExecution(
                pb.GetMutationExecutionRequest(context=context, idempotency_key=idempotency_key),
                timeout=self._timeout_seconds,
                metadata=self._metadata,
            )
        except grpc.aio.AioRpcError as error:
            self._raise_stable(error)
        return self._decode_mutation(response)

    @staticmethod
    def _decode_mutation(response: pb.MutationExecution) -> MutationResponse:
        data: Mapping[str, Any] | list[Any] | None = None
        if response.json_payload:
            decoded = json.loads(response.json_payload)
            if isinstance(decoded, (dict, list)):
                data = decoded
        status_name = pb.MutationExecutionStatus.Name(response.status)
        status_name = status_name.removeprefix("MUTATION_EXECUTION_STATUS_")
        return MutationResponse(
            execution_id=response.execution_id,
            approval_id=response.approval_id,
            investigation_id=response.investigation_id,
            tool_name=response.tool_name,
            target=response.target,
            parameters_hash=response.parameters_hash,
            idempotency_key=response.idempotency_key,
            status=status_name,
            data=data,
            safe_error=response.safe_error,
            replayed=response.replayed,
        )

    async def _dispatch(
        self, context: pb.RequestContext, tool_name: str, arguments: Mapping[str, Any]
    ) -> pb.ReadToolResponse:
        options = {"timeout": self._timeout_seconds, "metadata": self._metadata}
        if tool_name == "prometheus.query":
            prom_args = _PrometheusArgs.model_validate(arguments)
            return cast(
                pb.ReadToolResponse,
                await self._stub.QueryPrometheus(
                    pb.QueryPrometheusRequest(
                        context=context,
                        args=pb.QueryPrometheusArgs(
                            promql=prom_args.promql, at=_timestamp(prom_args.at)
                        ),
                    ),
                    **options,
                ),
            )
        if tool_name == "loki.query_range":
            loki_args = _LokiArgs.model_validate(arguments)
            return cast(
                pb.ReadToolResponse,
                await self._stub.QueryLoki(
                    pb.QueryLokiRequest(
                        context=context,
                        args=pb.QueryLokiArgs(
                            logql=loki_args.logql,
                            start=_timestamp(loki_args.start),
                            end=_timestamp(loki_args.end),
                            limit=loki_args.limit,
                            direction=loki_args.direction,
                        ),
                    ),
                    **options,
                ),
            )
        if tool_name == "tempo.get_trace":
            trace_args = _TempoTraceArgs.model_validate(arguments)
            return cast(
                pb.ReadToolResponse,
                await self._stub.GetTempoTrace(
                    pb.GetTempoTraceRequest(
                        context=context,
                        args=pb.GetTempoTraceArgs(trace_id=trace_args.trace_id),
                    ),
                    **options,
                ),
            )
        if tool_name == "tempo.search_traces":
            search_args = _TempoSearchArgs.model_validate(arguments)
            return cast(
                pb.ReadToolResponse,
                await self._stub.SearchTempoTraces(
                    pb.SearchTempoTracesRequest(
                        context=context,
                        args=pb.SearchTempoTracesArgs(
                            traceql=search_args.traceql,
                            start=_timestamp(search_args.start),
                            end=_timestamp(search_args.end),
                            limit=search_args.limit,
                        ),
                    ),
                    **options,
                ),
            )
        if tool_name == "releases.list":
            release_args = _ReleaseArgs.model_validate(arguments)
            return cast(
                pb.ReadToolResponse,
                await self._stub.ListReleases(
                    pb.ListReleasesRequest(
                        context=context,
                        args=pb.ListReleasesArgs(
                            service=release_args.service,
                            start=_timestamp(release_args.start),
                            end=_timestamp(release_args.end),
                            limit=release_args.limit,
                        ),
                    ),
                    **options,
                ),
            )
        if tool_name == "git.get_commit":
            git_args = _GitArgs.model_validate(arguments)
            return cast(
                pb.ReadToolResponse,
                await self._stub.GetGitCommit(
                    pb.GetGitCommitRequest(
                        context=context,
                        args=pb.GetGitCommitArgs(
                            revision=git_args.revision,
                            max_changed_files=git_args.max_changed_files,
                        ),
                    ),
                    **options,
                ),
            )
        if tool_name == "kubernetes.get_workload":
            workload_args = _WorkloadArgs.model_validate(arguments)
            return cast(
                pb.ReadToolResponse,
                await self._stub.GetKubernetesWorkload(
                    pb.GetKubernetesWorkloadRequest(
                        context=context,
                        args=pb.GetKubernetesWorkloadArgs(
                            namespace=workload_args.namespace,
                            kind=workload_args.kind,
                            name=workload_args.name,
                        ),
                    ),
                    **options,
                ),
            )
        if tool_name == "kubernetes.list_events":
            event_args = _EventArgs.model_validate(arguments)
            return cast(
                pb.ReadToolResponse,
                await self._stub.ListKubernetesEvents(
                    pb.ListKubernetesEventsRequest(
                        context=context,
                        args=pb.ListKubernetesEventsArgs(
                            namespace=event_args.namespace,
                            involved_object_kind=event_args.involved_object_kind,
                            involved_object_name=event_args.involved_object_name,
                            limit=event_args.limit,
                        ),
                    ),
                    **options,
                ),
            )
        raise LookupError(f"unregistered read tool: {tool_name}")

    @staticmethod
    def _decode(response: pb.ReadToolResponse) -> ToolResponse:
        data: Mapping[str, Any] | list[Any] | None = None
        if response.json_payload:
            decoded = json.loads(response.json_payload)
            if not isinstance(decoded, (dict, list)):
                raise ValueError("gateway payload must be a JSON object or array")
            data = decoded
        artifact = None
        if response.HasField("artifact"):
            artifact = ArtifactReference(
                uri=response.artifact.uri,
                sha256=response.artifact.sha256,
                size_bytes=response.artifact.size_bytes,
            )
        return ToolResponse(
            tool_name=response.tool_name,
            data=data,
            source_ref=response.source_ref,
            artifact=artifact,
            redacted=response.redacted,
        )

    @staticmethod
    def _raise_stable(error: grpc.aio.AioRpcError) -> NoReturn:
        rich_status = rpc_status.from_call(error)  # type: ignore[arg-type]
        if rich_status is not None:
            for detail in rich_status.details:
                tool_error = pb.ToolError()
                if detail.Is(tool_error.DESCRIPTOR):
                    detail.Unpack(tool_error)
                    raise ToolGatewayError(
                        pb.ToolErrorCode.Name(tool_error.code),
                        tool_error.safe_message,
                        tool_error.retryable,
                    ) from error
        raise ToolGatewayError(
            error.code().name, error.details() or "tool gateway call failed", False
        ) from error


def _timestamp(value: datetime | None) -> Timestamp | None:
    if value is None:
        return None
    timestamp = Timestamp()
    timestamp.FromDatetime(value)
    return timestamp
