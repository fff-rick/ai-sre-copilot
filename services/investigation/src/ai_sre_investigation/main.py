"""FastAPI application shell for the investigation service."""

from fastapi import FastAPI

from ai_sre_investigation import __version__
from ai_sre_investigation.config import Settings, get_settings
from ai_sre_investigation.models import HealthResponse


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application with explicit settings for deterministic tests."""

    active_settings = settings or get_settings()
    application = FastAPI(
        title="AI-SRE Investigation Service",
        version=__version__,
        docs_url="/docs" if active_settings.environment != "production" else None,
        redoc_url=None,
    )

    @application.get("/health/live", response_model=HealthResponse, tags=["health"])
    async def live() -> HealthResponse:
        return HealthResponse(
            service=active_settings.service_name,
            status="ok",
            environment=active_settings.environment,
        )

    @application.get("/health/ready", response_model=HealthResponse, tags=["health"])
    async def ready() -> HealthResponse:
        # Stage 0 has no mandatory downstream dependency in the request path.
        return HealthResponse(
            service=active_settings.service_name,
            status="ready",
            environment=active_settings.environment,
        )

    return application


app = create_app()
