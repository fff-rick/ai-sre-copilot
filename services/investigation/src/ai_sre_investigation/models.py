"""Small domain-neutral response models used by the HTTP shell."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ai_sre_investigation.domain import (
    Alert,
    Investigation,
    InvestigationBudget,
    InvestigationStatus,
)
from ai_sre_investigation.repository import InvestigationEvent


class HealthResponse(BaseModel):
    """Machine-readable service health response."""

    model_config = ConfigDict(frozen=True)

    service: str
    status: Literal["ok", "ready"]
    environment: str


class CreateInvestigationRequest(BaseModel):
    """Bounded alert intake contract."""

    model_config = ConfigDict(extra="forbid")

    alert: Alert
    budget: InvestigationBudget = InvestigationBudget()
    model_profile: str = "default"


class CancelResponse(BaseModel):
    investigation_id: str
    cancel_requested: bool


class InvestigationSummary(BaseModel):
    investigation: Investigation
    status: InvestigationStatus
    cancel_requested: bool
    last_error: str | None
    attempts: int


class InvestigationListResponse(BaseModel):
    items: list[InvestigationSummary]
    limit: int
    offset: int


class InvestigationTimelineResponse(BaseModel):
    items: list[InvestigationEvent]
    next_event_id: int


class EvidenceDetailResponse(BaseModel):
    investigation_id: str
    evidence: dict[str, Any]
