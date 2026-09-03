import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class MutationExecutionStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MUTATION_EXECUTION_STATUS_UNSPECIFIED: _ClassVar[MutationExecutionStatus]
    MUTATION_EXECUTION_STATUS_EXECUTING: _ClassVar[MutationExecutionStatus]
    MUTATION_EXECUTION_STATUS_SUCCEEDED: _ClassVar[MutationExecutionStatus]
    MUTATION_EXECUTION_STATUS_FAILED: _ClassVar[MutationExecutionStatus]

class ToolErrorCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TOOL_ERROR_CODE_UNSPECIFIED: _ClassVar[ToolErrorCode]
    TOOL_ERROR_CODE_INVALID_ARGUMENT: _ClassVar[ToolErrorCode]
    TOOL_ERROR_CODE_UNAUTHENTICATED: _ClassVar[ToolErrorCode]
    TOOL_ERROR_CODE_PERMISSION_DENIED: _ClassVar[ToolErrorCode]
    TOOL_ERROR_CODE_RATE_LIMITED: _ClassVar[ToolErrorCode]
    TOOL_ERROR_CODE_DEADLINE_EXCEEDED: _ClassVar[ToolErrorCode]
    TOOL_ERROR_CODE_SOURCE_UNAVAILABLE: _ClassVar[ToolErrorCode]
    TOOL_ERROR_CODE_NOT_FOUND: _ClassVar[ToolErrorCode]
    TOOL_ERROR_CODE_RESPONSE_TOO_LARGE: _ClassVar[ToolErrorCode]
    TOOL_ERROR_CODE_INTERNAL: _ClassVar[ToolErrorCode]
    TOOL_ERROR_CODE_CONFLICT: _ClassVar[ToolErrorCode]
MUTATION_EXECUTION_STATUS_UNSPECIFIED: MutationExecutionStatus
MUTATION_EXECUTION_STATUS_EXECUTING: MutationExecutionStatus
MUTATION_EXECUTION_STATUS_SUCCEEDED: MutationExecutionStatus
MUTATION_EXECUTION_STATUS_FAILED: MutationExecutionStatus
TOOL_ERROR_CODE_UNSPECIFIED: ToolErrorCode
TOOL_ERROR_CODE_INVALID_ARGUMENT: ToolErrorCode
TOOL_ERROR_CODE_UNAUTHENTICATED: ToolErrorCode
TOOL_ERROR_CODE_PERMISSION_DENIED: ToolErrorCode
TOOL_ERROR_CODE_RATE_LIMITED: ToolErrorCode
TOOL_ERROR_CODE_DEADLINE_EXCEEDED: ToolErrorCode
TOOL_ERROR_CODE_SOURCE_UNAVAILABLE: ToolErrorCode
TOOL_ERROR_CODE_NOT_FOUND: ToolErrorCode
TOOL_ERROR_CODE_RESPONSE_TOO_LARGE: ToolErrorCode
TOOL_ERROR_CODE_INTERNAL: ToolErrorCode
TOOL_ERROR_CODE_CONFLICT: ToolErrorCode

class CallerIdentity(_message.Message):
    __slots__ = ("actor_id", "role")
    ACTOR_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    actor_id: str
    role: str
    def __init__(self, actor_id: _Optional[str] = ..., role: _Optional[str] = ...) -> None: ...

class RequestContext(_message.Message):
    __slots__ = ("investigation_id", "trace_id", "caller")
    INVESTIGATION_ID_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    investigation_id: str
    trace_id: str
    caller: CallerIdentity
    def __init__(self, investigation_id: _Optional[str] = ..., trace_id: _Optional[str] = ..., caller: _Optional[_Union[CallerIdentity, _Mapping]] = ...) -> None: ...

class ListToolsRequest(_message.Message):
    __slots__ = ("context",)
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    context: RequestContext
    def __init__(self, context: _Optional[_Union[RequestContext, _Mapping]] = ...) -> None: ...

class ToolDescriptor(_message.Message):
    __slots__ = ("name", "version", "description")
    NAME_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    name: str
    version: str
    description: str
    def __init__(self, name: _Optional[str] = ..., version: _Optional[str] = ..., description: _Optional[str] = ...) -> None: ...

class ListToolsResponse(_message.Message):
    __slots__ = ("tools",)
    TOOLS_FIELD_NUMBER: _ClassVar[int]
    tools: _containers.RepeatedCompositeFieldContainer[ToolDescriptor]
    def __init__(self, tools: _Optional[_Iterable[_Union[ToolDescriptor, _Mapping]]] = ...) -> None: ...

class QueryPrometheusArgs(_message.Message):
    __slots__ = ("promql", "at")
    PROMQL_FIELD_NUMBER: _ClassVar[int]
    AT_FIELD_NUMBER: _ClassVar[int]
    promql: str
    at: _timestamp_pb2.Timestamp
    def __init__(self, promql: _Optional[str] = ..., at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class QueryPrometheusRequest(_message.Message):
    __slots__ = ("context", "args")
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    ARGS_FIELD_NUMBER: _ClassVar[int]
    context: RequestContext
    args: QueryPrometheusArgs
    def __init__(self, context: _Optional[_Union[RequestContext, _Mapping]] = ..., args: _Optional[_Union[QueryPrometheusArgs, _Mapping]] = ...) -> None: ...

class QueryLokiArgs(_message.Message):
    __slots__ = ("logql", "start", "end", "limit", "direction")
    LOGQL_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    DIRECTION_FIELD_NUMBER: _ClassVar[int]
    logql: str
    start: _timestamp_pb2.Timestamp
    end: _timestamp_pb2.Timestamp
    limit: int
    direction: str
    def __init__(self, logql: _Optional[str] = ..., start: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., end: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., limit: _Optional[int] = ..., direction: _Optional[str] = ...) -> None: ...

class QueryLokiRequest(_message.Message):
    __slots__ = ("context", "args")
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    ARGS_FIELD_NUMBER: _ClassVar[int]
    context: RequestContext
    args: QueryLokiArgs
    def __init__(self, context: _Optional[_Union[RequestContext, _Mapping]] = ..., args: _Optional[_Union[QueryLokiArgs, _Mapping]] = ...) -> None: ...

class GetTempoTraceArgs(_message.Message):
    __slots__ = ("trace_id",)
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    trace_id: str
    def __init__(self, trace_id: _Optional[str] = ...) -> None: ...

class GetTempoTraceRequest(_message.Message):
    __slots__ = ("context", "args")
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    ARGS_FIELD_NUMBER: _ClassVar[int]
    context: RequestContext
    args: GetTempoTraceArgs
    def __init__(self, context: _Optional[_Union[RequestContext, _Mapping]] = ..., args: _Optional[_Union[GetTempoTraceArgs, _Mapping]] = ...) -> None: ...

class SearchTempoTracesArgs(_message.Message):
    __slots__ = ("traceql", "start", "end", "limit")
    TRACEQL_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    traceql: str
    start: _timestamp_pb2.Timestamp
    end: _timestamp_pb2.Timestamp
    limit: int
    def __init__(self, traceql: _Optional[str] = ..., start: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., end: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., limit: _Optional[int] = ...) -> None: ...

class SearchTempoTracesRequest(_message.Message):
    __slots__ = ("context", "args")
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    ARGS_FIELD_NUMBER: _ClassVar[int]
    context: RequestContext
    args: SearchTempoTracesArgs
    def __init__(self, context: _Optional[_Union[RequestContext, _Mapping]] = ..., args: _Optional[_Union[SearchTempoTracesArgs, _Mapping]] = ...) -> None: ...

class ListReleasesArgs(_message.Message):
    __slots__ = ("service", "start", "end", "limit")
    SERVICE_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    service: str
    start: _timestamp_pb2.Timestamp
    end: _timestamp_pb2.Timestamp
    limit: int
    def __init__(self, service: _Optional[str] = ..., start: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., end: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., limit: _Optional[int] = ...) -> None: ...

class ListReleasesRequest(_message.Message):
    __slots__ = ("context", "args")
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    ARGS_FIELD_NUMBER: _ClassVar[int]
    context: RequestContext
    args: ListReleasesArgs
    def __init__(self, context: _Optional[_Union[RequestContext, _Mapping]] = ..., args: _Optional[_Union[ListReleasesArgs, _Mapping]] = ...) -> None: ...

class GetGitCommitArgs(_message.Message):
    __slots__ = ("revision", "max_changed_files")
    REVISION_FIELD_NUMBER: _ClassVar[int]
    MAX_CHANGED_FILES_FIELD_NUMBER: _ClassVar[int]
    revision: str
    max_changed_files: int
    def __init__(self, revision: _Optional[str] = ..., max_changed_files: _Optional[int] = ...) -> None: ...

class GetGitCommitRequest(_message.Message):
    __slots__ = ("context", "args")
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    ARGS_FIELD_NUMBER: _ClassVar[int]
    context: RequestContext
    args: GetGitCommitArgs
    def __init__(self, context: _Optional[_Union[RequestContext, _Mapping]] = ..., args: _Optional[_Union[GetGitCommitArgs, _Mapping]] = ...) -> None: ...

class GetKubernetesWorkloadArgs(_message.Message):
    __slots__ = ("namespace", "kind", "name")
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    namespace: str
    kind: str
    name: str
    def __init__(self, namespace: _Optional[str] = ..., kind: _Optional[str] = ..., name: _Optional[str] = ...) -> None: ...

class GetKubernetesWorkloadRequest(_message.Message):
    __slots__ = ("context", "args")
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    ARGS_FIELD_NUMBER: _ClassVar[int]
    context: RequestContext
    args: GetKubernetesWorkloadArgs
    def __init__(self, context: _Optional[_Union[RequestContext, _Mapping]] = ..., args: _Optional[_Union[GetKubernetesWorkloadArgs, _Mapping]] = ...) -> None: ...

class ListKubernetesEventsArgs(_message.Message):
    __slots__ = ("namespace", "involved_object_kind", "involved_object_name", "limit")
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    INVOLVED_OBJECT_KIND_FIELD_NUMBER: _ClassVar[int]
    INVOLVED_OBJECT_NAME_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    namespace: str
    involved_object_kind: str
    involved_object_name: str
    limit: int
    def __init__(self, namespace: _Optional[str] = ..., involved_object_kind: _Optional[str] = ..., involved_object_name: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

class ListKubernetesEventsRequest(_message.Message):
    __slots__ = ("context", "args")
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    ARGS_FIELD_NUMBER: _ClassVar[int]
    context: RequestContext
    args: ListKubernetesEventsArgs
    def __init__(self, context: _Optional[_Union[RequestContext, _Mapping]] = ..., args: _Optional[_Union[ListKubernetesEventsArgs, _Mapping]] = ...) -> None: ...

class ArtifactReference(_message.Message):
    __slots__ = ("uri", "sha256", "size_bytes")
    URI_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    SIZE_BYTES_FIELD_NUMBER: _ClassVar[int]
    uri: str
    sha256: str
    size_bytes: int
    def __init__(self, uri: _Optional[str] = ..., sha256: _Optional[str] = ..., size_bytes: _Optional[int] = ...) -> None: ...

class ReadToolResponse(_message.Message):
    __slots__ = ("tool_name", "source_ref", "json_payload", "artifact", "redacted")
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REF_FIELD_NUMBER: _ClassVar[int]
    JSON_PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    ARTIFACT_FIELD_NUMBER: _ClassVar[int]
    REDACTED_FIELD_NUMBER: _ClassVar[int]
    tool_name: str
    source_ref: str
    json_payload: bytes
    artifact: ArtifactReference
    redacted: bool
    def __init__(self, tool_name: _Optional[str] = ..., source_ref: _Optional[str] = ..., json_payload: _Optional[bytes] = ..., artifact: _Optional[_Union[ArtifactReference, _Mapping]] = ..., redacted: _Optional[bool] = ...) -> None: ...

class RestartDeploymentArgs(_message.Message):
    __slots__ = ("namespace", "name")
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    namespace: str
    name: str
    def __init__(self, namespace: _Optional[str] = ..., name: _Optional[str] = ...) -> None: ...

class ScaleDeploymentArgs(_message.Message):
    __slots__ = ("namespace", "name", "replicas")
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    REPLICAS_FIELD_NUMBER: _ClassVar[int]
    namespace: str
    name: str
    replicas: int
    def __init__(self, namespace: _Optional[str] = ..., name: _Optional[str] = ..., replicas: _Optional[int] = ...) -> None: ...

class RollbackDeploymentArgs(_message.Message):
    __slots__ = ("namespace", "name", "revision")
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    namespace: str
    name: str
    revision: int
    def __init__(self, namespace: _Optional[str] = ..., name: _Optional[str] = ..., revision: _Optional[int] = ...) -> None: ...

class ExecuteApprovedMutationRequest(_message.Message):
    __slots__ = ("context", "approval_token", "idempotency_key", "restart_deployment", "scale_deployment", "rollback_deployment")
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    APPROVAL_TOKEN_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    RESTART_DEPLOYMENT_FIELD_NUMBER: _ClassVar[int]
    SCALE_DEPLOYMENT_FIELD_NUMBER: _ClassVar[int]
    ROLLBACK_DEPLOYMENT_FIELD_NUMBER: _ClassVar[int]
    context: RequestContext
    approval_token: str
    idempotency_key: str
    restart_deployment: RestartDeploymentArgs
    scale_deployment: ScaleDeploymentArgs
    rollback_deployment: RollbackDeploymentArgs
    def __init__(self, context: _Optional[_Union[RequestContext, _Mapping]] = ..., approval_token: _Optional[str] = ..., idempotency_key: _Optional[str] = ..., restart_deployment: _Optional[_Union[RestartDeploymentArgs, _Mapping]] = ..., scale_deployment: _Optional[_Union[ScaleDeploymentArgs, _Mapping]] = ..., rollback_deployment: _Optional[_Union[RollbackDeploymentArgs, _Mapping]] = ...) -> None: ...

class GetMutationExecutionRequest(_message.Message):
    __slots__ = ("context", "idempotency_key")
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    context: RequestContext
    idempotency_key: str
    def __init__(self, context: _Optional[_Union[RequestContext, _Mapping]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class MutationExecution(_message.Message):
    __slots__ = ("execution_id", "approval_id", "investigation_id", "tool_name", "target", "parameters_hash", "idempotency_key", "status", "json_payload", "safe_error", "replayed", "started_at", "finished_at")
    EXECUTION_ID_FIELD_NUMBER: _ClassVar[int]
    APPROVAL_ID_FIELD_NUMBER: _ClassVar[int]
    INVESTIGATION_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_HASH_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    JSON_PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    SAFE_ERROR_FIELD_NUMBER: _ClassVar[int]
    REPLAYED_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    FINISHED_AT_FIELD_NUMBER: _ClassVar[int]
    execution_id: str
    approval_id: str
    investigation_id: str
    tool_name: str
    target: str
    parameters_hash: str
    idempotency_key: str
    status: MutationExecutionStatus
    json_payload: bytes
    safe_error: str
    replayed: bool
    started_at: _timestamp_pb2.Timestamp
    finished_at: _timestamp_pb2.Timestamp
    def __init__(self, execution_id: _Optional[str] = ..., approval_id: _Optional[str] = ..., investigation_id: _Optional[str] = ..., tool_name: _Optional[str] = ..., target: _Optional[str] = ..., parameters_hash: _Optional[str] = ..., idempotency_key: _Optional[str] = ..., status: _Optional[_Union[MutationExecutionStatus, str]] = ..., json_payload: _Optional[bytes] = ..., safe_error: _Optional[str] = ..., replayed: _Optional[bool] = ..., started_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., finished_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ToolError(_message.Message):
    __slots__ = ("code", "safe_message", "retryable")
    CODE_FIELD_NUMBER: _ClassVar[int]
    SAFE_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    code: ToolErrorCode
    safe_message: str
    retryable: bool
    def __init__(self, code: _Optional[_Union[ToolErrorCode, str]] = ..., safe_message: _Optional[str] = ..., retryable: _Optional[bool] = ...) -> None: ...
