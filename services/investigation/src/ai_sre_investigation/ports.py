"""Framework-independent ports for model and tool adapters."""

from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ModelRequest(BaseModel):
    """Vendor-neutral structured model request."""

    model_config = ConfigDict(frozen=True)

    system_instructions: str = Field(min_length=1, max_length=20_000)
    input_text: str = Field(min_length=1, max_length=100_000)
    response_schema: str = Field(min_length=1, max_length=200)
    response_json_schema: dict[str, Any] | None = None
    max_output_tokens: int = Field(default=4_096, ge=1, le=200_000)


class ModelResponse(BaseModel):
    """Validated response envelope returned by a model adapter."""

    model_config = ConfigDict(frozen=True)

    data: Mapping[str, Any]
    model_id: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ModelClient(Protocol):
    """Port implemented by real and fake model providers."""

    async def complete(self, request: ModelRequest) -> ModelResponse: ...


class ToolRequest(BaseModel):
    """Read-only tool request, kept separate from approval-gated mutation."""

    model_config = ConfigDict(frozen=True)

    investigation_id: str = Field(min_length=1, max_length=255)
    trace_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    arguments: Mapping[str, Any]


class ArtifactReference(BaseModel):
    """Immutable reference to a gateway-owned oversized result."""

    model_config = ConfigDict(frozen=True)

    uri: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)


class ToolResponse(BaseModel):
    """Bounded structured result returned by the trusted gateway."""

    model_config = ConfigDict(frozen=True)

    tool_name: str
    data: Mapping[str, Any] | list[Any] | None
    source_ref: str
    artifact: ArtifactReference | None = None
    redacted: bool = False


class ToolClient(Protocol):
    """Port for the Go tool gateway."""

    async def execute_read(self, request: ToolRequest) -> ToolResponse: ...


class MutationRequest(BaseModel):
    """Approval-gated request sent only to the trusted Go gateway."""

    model_config = ConfigDict(frozen=True)

    investigation_id: str = Field(min_length=1, max_length=255)
    trace_id: str = Field(min_length=1, max_length=128)
    actor_id: str = Field(min_length=1, max_length=255)
    approval_token: str = Field(min_length=1, max_length=512)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
    tool_name: str
    parameters: Mapping[str, Any]


class MutationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    execution_id: str
    approval_id: str
    investigation_id: str
    tool_name: str
    target: str
    parameters_hash: str
    idempotency_key: str
    status: str
    data: Mapping[str, Any] | list[Any] | None
    safe_error: str = ""
    replayed: bool = False


class MutationClient(Protocol):
    async def execute_mutation(self, request: MutationRequest) -> MutationResponse: ...
