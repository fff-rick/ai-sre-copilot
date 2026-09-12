"""Small domain-neutral response models used by the HTTP shell."""

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ai_sre_investigation.domain import (
    Alert,
    Investigation,
    InvestigationBudget,
    InvestigationStatus,
)
from ai_sre_investigation.remediation import RemediationAction
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


class AlertmanagerAlert(BaseModel):
    """Bounded subset of one Alertmanager webhook alert."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    status: Literal["firing", "resolved"]
    labels: dict[str, str] = Field(max_length=50)
    annotations: dict[str, str] = Field(default_factory=dict, max_length=50)
    starts_at: AwareDatetime = Field(alias="startsAt")
    generator_url: str = Field(default="", alias="generatorURL", max_length=2_000)
    fingerprint: str = Field(min_length=1, max_length=255)


class AlertmanagerWebhook(BaseModel):
    """Alertmanager webhook v4 payload used for automatic investigation intake."""

    model_config = ConfigDict(extra="ignore")

    status: Literal["firing", "resolved"]
    alerts: list[AlertmanagerAlert] = Field(max_length=100)


class AlertmanagerIngestResponse(BaseModel):
    accepted: int = Field(ge=0)
    investigation_ids: list[str]


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


class ProposeApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: RemediationAction


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expires_in_seconds: int = Field(default=900, ge=60, le=1_800)


class ExecuteRemediationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_token: str = Field(min_length=32, max_length=512)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
