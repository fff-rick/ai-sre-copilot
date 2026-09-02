"""Deterministic adapters used by tests and offline workflows."""

from collections.abc import Mapping

from ai_sre_investigation.ports import (
    ModelRequest,
    ModelResponse,
    ToolRequest,
    ToolResponse,
)


class FakeModelClient:
    """Return a pre-validated model response without network access."""

    def __init__(self, response: Mapping[str, object]) -> None:
        self._response = response
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            data=self._response,
            model_id="fake-model-v1",
            input_tokens=0,
            output_tokens=0,
        )


class FakeToolClient:
    """Resolve registered read tools from in-memory fixtures."""

    def __init__(self, responses: Mapping[str, Mapping[str, object]]) -> None:
        self._responses = responses
        self.requests: list[ToolRequest] = []

    async def execute_read(self, request: ToolRequest) -> ToolResponse:
        self.requests.append(request)
        if request.tool_name not in self._responses:
            raise LookupError(f"unregistered fake tool: {request.tool_name}")
        return ToolResponse(
            data=self._responses[request.tool_name],
            source_ref=f"fake://{request.tool_name}",
        )
