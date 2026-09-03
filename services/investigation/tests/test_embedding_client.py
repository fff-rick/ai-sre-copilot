import asyncio
import json

import httpx
import pytest

from ai_sre_investigation.embedding_client import (
    EmbeddingProviderError,
    HashEmbeddingClient,
    OpenAICompatibleEmbeddingClient,
)


def test_openai_compatible_embedding_success_and_request_shape() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer secret"
            body = json.loads(request.content)
            assert body == {"model": "embed-v1", "input": ["one", "two"], "dimensions": 3}
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "embedding": [0, 1, 0]},
                        {"index": 0, "embedding": [1, 0, 0]},
                    ]
                },
            )

        client = OpenAICompatibleEmbeddingClient(
            base_url="https://embedding.example/v1",
            api_key="secret",
            model="embed-v1",
            dimensions=3,
            transport=httpx.MockTransport(handler),
        )
        assert await client.embed(["one", "two"]) == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("status,retryable", [(429, True), (400, False)])
def test_embedding_http_errors_are_safe(status: int, retryable: bool) -> None:
    async def scenario() -> None:
        client = OpenAICompatibleEmbeddingClient(
            base_url="https://embedding.example",
            api_key="secret",
            model="embed-v1",
            transport=httpx.MockTransport(lambda _: httpx.Response(status, text="private detail")),
        )
        with pytest.raises(EmbeddingProviderError) as captured:
            await client.embed(["text"])
        assert captured.value.retryable is retryable
        assert "private detail" not in captured.value.safe_message
        await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"not_data": []}, "EMBEDDING_PROVIDER_ERROR"),
        ({"data": [{"index": 2, "embedding": [1]}]}, "EMBEDDING_RESULT_MISMATCH"),
        ({"data": [{"index": 0, "embedding": [float("nan")]}]}, "EMBEDDING_PROVIDER_ERROR"),
    ],
)
def test_embedding_invalid_provider_payload(payload: object, code: str) -> None:
    async def scenario() -> None:
        client = OpenAICompatibleEmbeddingClient(
            base_url="https://embedding.example",
            api_key="secret",
            model="embed-v1",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
        )
        with pytest.raises(EmbeddingProviderError) as captured:
            await client.embed(["text"])
        assert captured.value.code == code
        await client.close()

    asyncio.run(scenario())


def test_embedding_rejects_non_finite_values_after_parsing() -> None:
    async def scenario() -> None:
        client = OpenAICompatibleEmbeddingClient(
            base_url="https://embedding.example",
            api_key="secret",
            model="embed-v1",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=b'{"data":[{"index":0,"embedding":[1e999]}]}')
            ),
        )
        with pytest.raises(EmbeddingProviderError) as captured:
            await client.embed(["text"])
        assert captured.value.code == "EMBEDDING_RESULT_INVALID"
        await client.close()

    asyncio.run(scenario())


def test_embedding_configuration_input_and_hash_guards() -> None:
    with pytest.raises(ValueError, match="http or https"):
        OpenAICompatibleEmbeddingClient(base_url="file:///tmp", api_key="x", model="x")
    with pytest.raises(ValueError, match="dimensions"):
        HashEmbeddingClient(4)

    async def scenario() -> None:
        client = OpenAICompatibleEmbeddingClient(
            base_url="https://embedding.example",
            api_key="secret",
            model="embed-v1",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": []})),
        )
        with pytest.raises(ValueError, match="batch size"):
            await client.embed([])
        with pytest.raises(ValueError, match="characters"):
            await client.embed([""])
        await client.close()

        hashed = HashEmbeddingClient(8)
        values = await hashed.embed(["same tokens", "same tokens", ""])
        assert values[0] == values[1]
        assert values[2] == [0.0] * 8

    asyncio.run(scenario())
