"""FastAPI entrypoint for health and investigation lifecycle APIs."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status

from ai_sre_investigation import __version__
from ai_sre_investigation.config import Settings, get_settings
from ai_sre_investigation.models import (
    CancelResponse,
    CreateInvestigationRequest,
    HealthResponse,
)
from ai_sre_investigation.repository import StoredInvestigation
from ai_sre_investigation.runtime import production_service
from ai_sre_investigation.service import InvestigationService


def create_app(
    settings: Settings | None = None, service: InvestigationService | None = None
) -> FastAPI:
    """Create an application with explicit settings for deterministic tests."""

    active_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is not None:
            await service.start()
            app.state.investigation_service = service
            try:
                yield
            finally:
                await service.stop()
            return
        async with production_service(active_settings) as configured_service:
            app.state.investigation_service = configured_service
            yield

    application = FastAPI(
        title="AI-SRE Investigation Service",
        version=__version__,
        docs_url="/docs" if active_settings.environment != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
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
        return HealthResponse(
            service=active_settings.service_name,
            status="ready",
            environment=active_settings.environment,
        )

    @application.post(
        "/api/v1/investigations",
        response_model=StoredInvestigation,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["investigations"],
    )
    async def create_investigation(
        request: CreateInvestigationRequest,
    ) -> StoredInvestigation:
        active_service: InvestigationService | None = getattr(
            application.state, "investigation_service", service
        )
        if active_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="durable investigation runtime is not configured",
            )
        return await active_service.create(
            request.alert, budget=request.budget, model_profile=request.model_profile
        )

    @application.get(
        "/api/v1/investigations/{investigation_id}",
        response_model=StoredInvestigation,
        tags=["investigations"],
    )
    async def get_investigation(investigation_id: str) -> StoredInvestigation:
        active_service: InvestigationService | None = getattr(
            application.state, "investigation_service", service
        )
        if active_service is None:
            raise HTTPException(status_code=503, detail="runtime is not configured")
        record = await active_service.get(investigation_id)
        if record is None:
            raise HTTPException(status_code=404, detail="investigation not found")
        return record

    @application.post(
        "/api/v1/investigations/{investigation_id}/cancel",
        response_model=CancelResponse,
        tags=["investigations"],
    )
    async def cancel_investigation(investigation_id: str) -> CancelResponse:
        active_service: InvestigationService | None = getattr(
            application.state, "investigation_service", service
        )
        if active_service is None:
            raise HTTPException(status_code=503, detail="runtime is not configured")
        changed = await active_service.cancel(investigation_id)
        if not changed:
            record = await active_service.get(investigation_id)
            if record is None:
                raise HTTPException(status_code=404, detail="investigation not found")
        return CancelResponse(investigation_id=investigation_id, cancel_requested=changed)

    return application


app = create_app()
