"""OpenAI-compatible embedding adapter and deterministic offline implementation."""

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class EmbeddingProviderError(RuntimeError):
    def __init__(self, code: str, safe_message: str, retryable: bool) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


class _EmbeddingItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int = Field(ge=0)
    embedding: list[float] = Field(min_length=1, max_length=4_096)


class _EmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[_EmbeddingItem]


class OpenAICompatibleEmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30,
        dimensions: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("embedding base_url must use http or https")
        self._model = model
        self._dimensions = dimensions
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
            transport=transport,
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts or len(texts) > 128:
            raise ValueError("embedding batch size must be between 1 and 128")
        if any(not text or len(text) > 20_000 for text in texts):
            raise ValueError("embedding input must contain 1 to 20000 characters")
        body: dict[str, Any] = {"model": self._model, "input": list(texts)}
        if self._dimensions is not None:
            body["dimensions"] = self._dimensions
        try:
            response = await self._client.post("/embeddings", json=body)
            response.raise_for_status()
            parsed = _EmbeddingResponse.model_validate(response.json())
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            raise EmbeddingProviderError(
                f"EMBEDDING_HTTP_{status}",
                "embedding provider request failed",
                status in {408, 429, 500, 502, 503, 504},
            ) from error
        except (httpx.HTTPError, ValidationError, ValueError) as error:
            raise EmbeddingProviderError(
                "EMBEDDING_PROVIDER_ERROR", "embedding provider response was unavailable", True
            ) from error
        ordered = sorted(parsed.data, key=lambda item: item.index)
        if [item.index for item in ordered] != list(range(len(texts))):
            raise EmbeddingProviderError(
                "EMBEDDING_RESULT_MISMATCH", "embedding provider result count was invalid", False
            )
        if any(any(not math.isfinite(value) for value in item.embedding) for item in ordered):
            raise EmbeddingProviderError(
                "EMBEDDING_RESULT_INVALID", "embedding provider returned invalid values", False
            )
        return [item.embedding for item in ordered]

    async def close(self) -> None:
        await self._client.aclose()


class HashEmbeddingClient:
    """Stable feature-hashing embedding for tests and offline retrieval evaluation only."""

    def __init__(self, dimensions: int = 64) -> None:
        if dimensions < 8 or dimensions > 4_096:
            raise ValueError("dimensions must be between 8 and 4096")
        self._dimensions = dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        result: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self._dimensions
            for token in re.findall(r"[\w.-]+", text.lower(), re.UNICODE):
                digest = hashlib.sha256(token.encode()).digest()
                index = int.from_bytes(digest[:4], "big") % self._dimensions
                vector[index] += 1.0 if digest[4] & 1 else -1.0
            norm = math.sqrt(sum(value * value for value in vector))
            result.append([value / norm for value in vector] if norm else vector)
        return result
