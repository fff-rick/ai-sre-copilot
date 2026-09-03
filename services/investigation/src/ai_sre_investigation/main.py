"""FastAPI entrypoint for health and investigation lifecycle APIs."""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from ai_sre_investigation import __version__
from ai_sre_investigation.config import Settings, get_settings
from ai_sre_investigation.models import (
    ApproveRequest,
    CancelResponse,
    CreateInvestigationRequest,
    EvidenceDetailResponse,
    ExecuteRemediationRequest,
    HealthResponse,
    InvestigationListResponse,
    InvestigationSummary,
    InvestigationTimelineResponse,
    ProposeApprovalRequest,
)
from ai_sre_investigation.remediation import (
    ApprovalGrant,
    RemediationAction,
    RemediationApproval,
    RemediationAuditEvent,
    RemediationExecution,
)
from ai_sre_investigation.remediation_service import RemediationService
from ai_sre_investigation.repository import StoredInvestigation
from ai_sre_investigation.runtime import production_service
from ai_sre_investigation.service import InvestigationService
from ai_sre_investigation.tool_gateway_client import ToolGatewayError


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
        "/api/v1/investigations",
        response_model=InvestigationListResponse,
        tags=["investigations"],
    )
    async def list_investigations(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100_000),
    ) -> InvestigationListResponse:
        active_service = _service(application, service)
        records = await active_service.list_investigations(limit=limit, offset=offset)
        return InvestigationListResponse(
            items=[
                InvestigationSummary(
                    investigation=record.investigation,
                    status=record.status,
                    cancel_requested=record.cancel_requested,
                    last_error=record.last_error,
                    attempts=record.attempts,
                )
                for record in records
            ],
            limit=limit,
            offset=offset,
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

    @application.get(
        "/api/v1/investigations/{investigation_id}/timeline",
        response_model=InvestigationTimelineResponse,
        tags=["investigations"],
    )
    async def get_timeline(
        investigation_id: str,
        after_event_id: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> InvestigationTimelineResponse:
        active_service = _service(application, service)
        if await active_service.get(investigation_id) is None:
            raise HTTPException(status_code=404, detail="investigation not found")
        events = await active_service.events(investigation_id, after_event_id, limit)
        return InvestigationTimelineResponse(
            items=events,
            next_event_id=events[-1].event_id if events else after_event_id,
        )

    @application.get(
        "/api/v1/investigations/{investigation_id}/evidence/{evidence_id}",
        response_model=EvidenceDetailResponse,
        tags=["investigations"],
    )
    async def get_evidence(investigation_id: str, evidence_id: str) -> EvidenceDetailResponse:
        active_service = _service(application, service)
        record = await active_service.get(investigation_id)
        if record is None:
            raise HTTPException(status_code=404, detail="investigation not found")
        report = record.report or {}
        evidence = next(
            (
                item
                for item in cast(list[dict[str, Any]], report.get("evidence", []))
                if item.get("evidence_id") == evidence_id
            ),
            None,
        )
        if evidence is None:
            raise HTTPException(status_code=404, detail="evidence not found")
        return EvidenceDetailResponse(investigation_id=investigation_id, evidence=evidence)

    @application.get(
        "/api/v1/investigations/{investigation_id}/events",
        tags=["investigations"],
    )
    async def stream_events(
        investigation_id: str,
        request: Request,
        last_event_id: int | None = Header(default=None, alias="Last-Event-ID", ge=0),
    ) -> StreamingResponse:
        active_service = _service(application, service)
        if await active_service.get(investigation_id) is None:
            raise HTTPException(status_code=404, detail="investigation not found")

        async def event_stream() -> AsyncIterator[str]:
            cursor = last_event_id or 0
            last_heartbeat = time.monotonic()
            while not await request.is_disconnected():
                events = await active_service.events(investigation_id, cursor, 100)
                for event in events:
                    cursor = event.event_id
                    payload = json.dumps(
                        event.model_dump(mode="json"),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    yield (
                        f"id: {event.event_id}\n"
                        f"event: investigation\n"
                        "retry: 1000\n"
                        f"data: {payload}\n\n"
                    )
                record = await active_service.get(investigation_id)
                if record is None or (
                    record.status.value in {"COMPLETED", "CANCELLED", "FAILED"}
                    and len(events) < 100
                ):
                    return
                if time.monotonic() - last_heartbeat >= 15:
                    yield ": keep-alive\n\n"
                    last_heartbeat = time.monotonic()
                await asyncio.sleep(0.25)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

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

    @application.post(
        "/api/v1/investigations/{investigation_id}/approvals",
        response_model=RemediationApproval,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["remediation"],
    )
    async def propose_remediation(
        investigation_id: str,
        request: ProposeApprovalRequest,
        actor_id: str = Header(alias="X-Actor-ID"),
        actor_role: str = Header(alias="X-Actor-Role"),
    ) -> RemediationApproval:
        remediation = _remediation(application, service)
        return await _remediation_call(
            remediation.propose(investigation_id, request.action, actor_id, actor_role)
        )

    @application.get(
        "/api/v1/investigations/{investigation_id}/approvals",
        response_model=list[RemediationApproval],
        tags=["remediation"],
    )
    async def list_approvals(investigation_id: str) -> list[RemediationApproval]:
        return await _remediation_call(
            _remediation(application, service).list_approvals(investigation_id)
        )

    @application.post(
        "/api/v1/investigations/{investigation_id}/approvals/{approval_id}/approve",
        response_model=ApprovalGrant,
        tags=["remediation"],
    )
    async def approve_remediation(
        investigation_id: str,
        approval_id: str,
        request: ApproveRequest,
        actor_id: str = Header(alias="X-Actor-ID"),
        actor_role: str = Header(alias="X-Actor-Role"),
    ) -> ApprovalGrant:
        return await _remediation_call(
            _remediation(application, service).approve(
                investigation_id,
                approval_id,
                actor_id,
                actor_role,
                request.expires_in_seconds,
            )
        )

    @application.put(
        "/api/v1/investigations/{investigation_id}/approvals/{approval_id}",
        response_model=RemediationApproval,
        tags=["remediation"],
    )
    async def modify_remediation(
        investigation_id: str,
        approval_id: str,
        action: RemediationAction,
        actor_id: str = Header(alias="X-Actor-ID"),
        actor_role: str = Header(alias="X-Actor-Role"),
    ) -> RemediationApproval:
        return await _remediation_call(
            _remediation(application, service).modify(
                investigation_id, approval_id, action, actor_id, actor_role
            )
        )

    @application.post(
        "/api/v1/investigations/{investigation_id}/approvals/{approval_id}/reject",
        response_model=RemediationApproval,
        tags=["remediation"],
    )
    async def reject_remediation(
        investigation_id: str,
        approval_id: str,
        actor_id: str = Header(alias="X-Actor-ID"),
        actor_role: str = Header(alias="X-Actor-Role"),
    ) -> RemediationApproval:
        return await _remediation_call(
            _remediation(application, service).reject(
                investigation_id, approval_id, actor_id, actor_role
            )
        )

    @application.post(
        "/api/v1/investigations/{investigation_id}/approvals/{approval_id}/execute",
        response_model=RemediationExecution,
        tags=["remediation"],
    )
    async def execute_remediation(
        investigation_id: str,
        approval_id: str,
        request: ExecuteRemediationRequest,
        actor_id: str = Header(alias="X-Actor-ID"),
        actor_role: str = Header(alias="X-Actor-Role"),
    ) -> RemediationExecution:
        return await _remediation_call(
            _remediation(application, service).execute(
                investigation_id,
                approval_id,
                request.approval_token,
                request.idempotency_key,
                actor_id,
                actor_role,
            )
        )

    @application.get(
        "/api/v1/investigations/{investigation_id}/remediation-audit",
        response_model=list[RemediationAuditEvent],
        tags=["remediation"],
    )
    async def remediation_audit(
        investigation_id: str,
    ) -> list[RemediationAuditEvent]:
        return await _remediation_call(_remediation(application, service).audit(investigation_id))

    return application


def _service(application: FastAPI, fallback: InvestigationService | None) -> InvestigationService:
    active_service: InvestigationService | None = getattr(
        application.state, "investigation_service", fallback
    )
    if active_service is None:
        raise HTTPException(status_code=503, detail="runtime is not configured")
    return active_service


def _remediation(application: FastAPI, fallback: InvestigationService | None) -> RemediationService:
    remediation = _service(application, fallback).remediation
    if remediation is None:
        raise HTTPException(status_code=503, detail="remediation runtime is not configured")
    return remediation


async def _remediation_call[ResponseT](awaitable: Awaitable[ResponseT]) -> ResponseT:
    try:
        return await awaitable
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ToolGatewayError as error:
        if error.code in {
            "TOOL_ERROR_CODE_UNAUTHENTICATED",
            "TOOL_ERROR_CODE_PERMISSION_DENIED",
        }:
            http_status = 403
        elif error.code == "TOOL_ERROR_CODE_CONFLICT":
            http_status = 409
        elif error.code == "TOOL_ERROR_CODE_INVALID_ARGUMENT":
            http_status = 422
        elif error.retryable:
            http_status = 503
        else:
            http_status = 502
        raise HTTPException(status_code=http_status, detail=error.safe_message) from error


app = create_app()
