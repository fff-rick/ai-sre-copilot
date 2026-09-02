import asyncio

import httpx
from fastapi import FastAPI

from ai_sre_investigation.config import Settings
from ai_sre_investigation.main import create_app


def get(app: FastAPI, path: str) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(request())


def test_liveness_is_machine_readable() -> None:
    response = get(create_app(Settings(environment="test")), "/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "service": "investigation-service",
        "status": "ok",
        "environment": "test",
    }


def test_readiness_is_available_without_optional_dependencies() -> None:
    response = get(
        create_app(Settings(environment="test", database_url=None)),
        "/health/ready",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_openapi_docs_are_disabled_in_production() -> None:
    assert get(create_app(Settings(environment="production")), "/docs").status_code == 404
