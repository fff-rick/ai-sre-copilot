"""Deterministic evaluation primitives for frozen, redacted tool replay."""

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ai_sre_investigation.ports import ArtifactReference, ToolClient, ToolRequest, ToolResponse
from ai_sre_investigation.tool_gateway_client import ToolGatewayError

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|"
    r"secret|cookie|credential|private[_-]?key)",
    re.IGNORECASE,
)
_SENSITIVE_VALUES = (
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)[?&](?:token|api[_-]?key|signature)=[^&\s]+"),
)


class FailureCategory(StrEnum):
    """Stable failure taxonomy used by reports and CI gates."""

    RETRIEVAL = "retrieval_failure"
    TOOL = "tool_failure"
    REASONING = "reasoning_failure"
    CITATION = "citation_failure"
    PERMISSION = "permission_failure"
    BUDGET = "budget_failure"


class RecordedToolExchange(BaseModel):
    """One sanitized tool request and its response or stable failure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    arguments: dict[str, Any]
    response_data: dict[str, Any] | list[Any] | None = None
    source_ref: str = Field(default="", max_length=2_000)
    redacted: bool = False
    error_code: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=500)
    retryable: bool = False
    artifact: ArtifactReference | None = None

    @model_validator(mode="after")
    def valid_outcome(self) -> Self:
        if self.error_code is None and not self.source_ref:
            raise ValueError("successful exchange requires source_ref")
        if self.error_code is not None and not self.error_message:
            raise ValueError("failed exchange requires error_message")
        return self


class FrozenToolRecording(BaseModel):
    """Immutable recording whose checksum covers all replayable content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    recording_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{2,127}$")
    dataset_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=128)
    captured_at: AwareDatetime
    frozen_at: AwareDatetime
    exchanges: tuple[RecordedToolExchange, ...] = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def checksum_matches(self) -> Self:
        expected = recording_checksum(
            recording_id=self.recording_id,
            dataset_id=self.dataset_id,
            case_id=self.case_id,
            captured_at=self.captured_at,
            frozen_at=self.frozen_at,
            exchanges=self.exchanges,
        )
        if self.content_sha256 != expected:
            raise ValueError("recording checksum mismatch")
        return self

    @property
    def reference(self) -> str:
        return f"recording://{self.dataset_id}/{self.case_id}/{self.content_sha256}"


def sanitize_recording_value(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact common credential keys and token-shaped string values."""

    if key is not None and _SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): sanitize_recording_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list | tuple):
        return [sanitize_recording_value(item) for item in value]
    if isinstance(value, str):
        sanitized = value
        for pattern in _SENSITIVE_VALUES:
            sanitized = pattern.sub(REDACTED, sanitized)
        return sanitized
    return value


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def recording_checksum(
    *,
    recording_id: str,
    dataset_id: str,
    case_id: str,
    captured_at: datetime,
    frozen_at: datetime,
    exchanges: tuple[RecordedToolExchange, ...] | list[RecordedToolExchange],
) -> str:
    payload = {
        "schema_version": 1,
        "recording_id": recording_id,
        "dataset_id": dataset_id,
        "case_id": case_id,
        "captured_at": captured_at.isoformat(),
        "frozen_at": frozen_at.isoformat(),
        "exchanges": [item.model_dump(mode="json") for item in exchanges],
    }
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def freeze_tool_recording(
    *,
    recording_id: str,
    dataset_id: str,
    case_id: str,
    exchanges: list[RecordedToolExchange],
    captured_at: datetime,
    frozen_at: datetime | None = None,
) -> FrozenToolRecording:
    """Sanitize exchanges and seal them with a content checksum."""

    frozen_time = frozen_at or datetime.now(UTC)
    sanitized = tuple(
        RecordedToolExchange.model_validate(
            sanitize_recording_value(item.model_dump(mode="python"))
        )
        for item in exchanges
    )
    checksum = recording_checksum(
        recording_id=recording_id,
        dataset_id=dataset_id,
        case_id=case_id,
        captured_at=captured_at,
        frozen_at=frozen_time,
        exchanges=sanitized,
    )
    return FrozenToolRecording(
        recording_id=recording_id,
        dataset_id=dataset_id,
        case_id=case_id,
        captured_at=captured_at,
        frozen_at=frozen_time,
        exchanges=sanitized,
        content_sha256=checksum,
    )


class RecordingToolClient:
    """Decorate a live client and retain only bounded, sanitized replay data."""

    def __init__(
        self,
        inner: ToolClient,
        *,
        dataset_id: str,
        case_id: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._inner = inner
        self._dataset_id = dataset_id
        self._case_id = case_id
        self._now = now
        self._captured_at = now()
        self.exchanges: list[RecordedToolExchange] = []

    async def execute_read(self, request: ToolRequest) -> ToolResponse:
        arguments = sanitize_recording_value(dict(request.arguments))
        try:
            response = await self._inner.execute_read(request)
        except ToolGatewayError as error:
            self.exchanges.append(
                RecordedToolExchange(
                    tool_name=request.tool_name,
                    arguments=arguments,
                    error_code=error.code,
                    error_message=error.safe_message,
                    retryable=error.retryable,
                )
            )
            raise
        except Exception as error:
            self.exchanges.append(
                RecordedToolExchange(
                    tool_name=request.tool_name,
                    arguments=arguments,
                    error_code=type(error).__name__.upper(),
                    error_message="tool call failed during recording",
                )
            )
            raise
        data = sanitize_recording_value(response.data)
        source_ref = sanitize_recording_value(response.source_ref)
        artifact = (
            ArtifactReference.model_validate(
                sanitize_recording_value(response.artifact.model_dump(mode="python"))
            )
            if response.artifact is not None
            else None
        )
        exchange = RecordedToolExchange(
            tool_name=request.tool_name,
            arguments=arguments,
            response_data=data,
            source_ref=source_ref,
            redacted=response.redacted
            or data != response.data
            or source_ref != response.source_ref
            or artifact != response.artifact,
            artifact=artifact,
        )
        self.exchanges.append(exchange)
        return ToolResponse(
            tool_name=response.tool_name,
            data=exchange.response_data,
            source_ref=exchange.source_ref,
            artifact=exchange.artifact,
            redacted=exchange.redacted,
        )

    def freeze(
        self, recording_id: str, *, frozen_at: datetime | None = None
    ) -> FrozenToolRecording:
        return freeze_tool_recording(
            recording_id=recording_id,
            dataset_id=self._dataset_id,
            case_id=self._case_id,
            exchanges=self.exchanges,
            captured_at=self._captured_at,
            frozen_at=frozen_at,
        )


class ReplayToolClient:
    """Replay a checksum-verified recording and reject unrecorded tool requests."""

    def __init__(self, recording: FrozenToolRecording) -> None:
        self.recording = recording
        self.requests: list[ToolRequest] = []
        self._exchanges: dict[str, list[RecordedToolExchange]] = defaultdict(list)
        for exchange in recording.exchanges:
            key = self._key(exchange.tool_name, exchange.arguments)
            self._exchanges[key].append(exchange)

    @staticmethod
    def _key(tool_name: str, arguments: Mapping[str, Any]) -> str:
        sanitized = sanitize_recording_value(dict(arguments))
        return f"{tool_name}:{_canonical(sanitized)}"

    async def execute_read(self, request: ToolRequest) -> ToolResponse:
        self.requests.append(request)
        key = self._key(request.tool_name, request.arguments)
        candidates = self._exchanges.get(key, [])
        if not candidates:
            raise ToolGatewayError(
                "REPLAY_MISS",
                f"no frozen response for {request.tool_name}",
                False,
            )
        exchange = candidates.pop(0)
        if exchange.error_code is not None:
            raise ToolGatewayError(
                exchange.error_code,
                exchange.error_message or "recorded tool failure",
                exchange.retryable,
            )
        return ToolResponse(
            tool_name=exchange.tool_name,
            data=exchange.response_data,
            source_ref=exchange.source_ref,
            redacted=exchange.redacted,
            artifact=exchange.artifact,
        )
