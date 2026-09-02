import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

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

class ToolError(_message.Message):
    __slots__ = ("code", "safe_message", "retryable")
    CODE_FIELD_NUMBER: _ClassVar[int]
    SAFE_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    code: ToolErrorCode
    safe_message: str
    retryable: bool
    def __init__(self, code: _Optional[_Union[ToolErrorCode, str]] = ..., safe_message: _Optional[str] = ..., retryable: _Optional[bool] = ...) -> None: ...
