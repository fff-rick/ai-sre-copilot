"""Deterministic adapters used by tests and offline workflows."""

from collections.abc import Mapping, Sequence

from ai_sre_investigation.ports import (
    ModelRequest,
    ModelResponse,
    ToolRequest,
    ToolResponse,
)


class FakeModelClient:
    """Return a pre-validated model response without network access."""

    def __init__(self, response: Mapping[str, object] | Sequence[Mapping[str, object]]) -> None:
        self._responses = list(response) if isinstance(response, Sequence) else [response]
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        return ModelResponse(
            data=self._responses[index],
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
            tool_name=request.tool_name,
            data=self._responses[request.tool_name],
            source_ref=f"fake://{request.tool_name}",
        )


class FailingFakeToolClient(FakeToolClient):
    """Inject stable per-source failures while preserving other responses."""

    def __init__(
        self,
        responses: Mapping[str, Mapping[str, object]],
        failures: Mapping[str, Exception],
    ) -> None:
        super().__init__(responses)
        self._failures = failures

    async def execute_read(self, request: ToolRequest) -> ToolResponse:
        if request.tool_name in self._failures:
            self.requests.append(request)
            raise self._failures[request.tool_name]
        return await super().execute_read(request)
