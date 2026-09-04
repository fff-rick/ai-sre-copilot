import asyncio
import json

import httpx
import pytest

from ai_sre_investigation.model_client import (
    ModelProviderError,
    OpenAICompatibleModelClient,
)
from ai_sre_investigation.ports import ModelRequest

REQUEST = ModelRequest(
    system_instructions="Return structured hypotheses.",
    input_text="bounded evidence",
    response_schema="HypothesisProposalBatchV1",
    response_json_schema={
        "type": "object",
        "properties": {"hypotheses": {"type": "array", "items": {"type": "object"}}},
        "required": ["hypotheses"],
        "additionalProperties": False,
    },
)


def test_openai_compatible_adapter_parses_json_and_usage() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret"
        body = json.loads(request.content)
        assert body["response_format"] == {
            "type": "json_schema",
            "json_schema": {
                "name": "HypothesisProposalBatchV1",
                "strict": True,
                "schema": REQUEST.response_json_schema,
            },
        }
        return httpx.Response(
            200,
            json={
                "model": "provider-model-1",
                "choices": [{"message": {"content": '{"hypotheses":[]}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://models.example/v1/"
        ) as transport_client:
            client = OpenAICompatibleModelClient(
                base_url="https://models.example/v1",
                api_key="secret",
                model="configured-model",
                client=transport_client,
            )
            assert client.model_id == "configured-model"
            response = await client.complete(REQUEST)
        assert response.data == {"hypotheses": []}
        assert response.model_id == "provider-model-1"
        assert response.input_tokens == 12
        assert response.output_tokens == 4

    asyncio.run(scenario())


def test_model_provider_errors_are_safe_and_typed() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"secret_provider_detail": "do not expose"})

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://models.example/v1/"
        ) as transport_client:
            client = OpenAICompatibleModelClient(
                base_url="https://models.example/v1",
                api_key="secret",
                model="configured-model",
                client=transport_client,
            )
            with pytest.raises(ModelProviderError) as captured:
                await client.complete(REQUEST)
        assert captured.value.code == "HTTP_429"
        assert captured.value.retryable
        assert "secret_provider_detail" not in str(captured.value)

    asyncio.run(scenario())


def test_adapter_retains_json_object_fallback_without_a_schema() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "model": "compat-model",
                "choices": [{"message": {"content": "{}"}}],
                "usage": {},
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://models.example/v1/"
        ) as transport_client:
            client = OpenAICompatibleModelClient(
                base_url="https://models.example/v1",
                api_key="secret",
                model="compat-model",
                client=transport_client,
            )
            response = await client.complete(
                ModelRequest(
                    system_instructions="Return JSON.",
                    input_text="input",
                    response_schema="GenericObjectV1",
                )
            )
        assert response.data == {}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("base_url", "api_key", "model"),
    [
        ("file:///tmp/model", "key", "model"),
        ("https://example", "", "model"),
        ("https://example", "key", ""),
    ],
)
def test_model_configuration_is_validated(base_url: str, api_key: str, model: str) -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleModelClient(base_url=base_url, api_key=api_key, model=model)
