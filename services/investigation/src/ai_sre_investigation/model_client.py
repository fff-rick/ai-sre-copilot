"""OpenAI-compatible, vendor-neutral structured model adapter."""

from typing import Any

import httpx

from ai_sre_investigation.ports import ModelRequest, ModelResponse


class ModelProviderError(RuntimeError):
    """Safe model-provider failure with retry semantics."""

    def __init__(self, code: str, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable


class OpenAICompatibleModelClient:
    """Call a Chat Completions compatible endpoint and require a JSON object."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("model base_url must be HTTP(S)")
        if not api_key:
            raise ValueError("model api_key is required")
        if not model:
            raise ValueError("model identifier is required")
        self._model = model
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
        )

    @property
    def model_id(self) -> str:
        return self._model

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def complete(self, request: ModelRequest) -> ModelResponse:
        response_format: dict[str, Any]
        if request.response_json_schema is not None:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.response_schema,
                    "strict": True,
                    "schema": request.response_json_schema,
                },
            }
        else:
            response_format = {"type": "json_object"}
        try:
            response = await self._client.post(
                "chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "temperature": 0,
                    "max_tokens": request.max_output_tokens,
                    "response_format": response_format,
                    "messages": [
                        {"role": "system", "content": request.system_instructions},
                        {"role": "user", "content": request.input_text},
                    ],
                },
            )
            response.raise_for_status()
            body: dict[str, Any] = response.json()
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("model content is not text")
            import json

            data = json.loads(content)
            if not isinstance(data, dict):
                raise TypeError("model response must be a JSON object")
            usage = body.get("usage", {})
            return ModelResponse(
                data=data,
                model_id=str(body.get("model", self._model)),
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
            )
        except httpx.HTTPStatusError as error:
            retryable = (
                error.response.status_code in {408, 409, 429} or error.response.status_code >= 500
            )
            raise ModelProviderError(
                f"HTTP_{error.response.status_code}",
                "model provider rejected the request",
                retryable,
            ) from error
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise ModelProviderError(
                "INVALID_MODEL_RESPONSE", "model provider call failed", False
            ) from error
