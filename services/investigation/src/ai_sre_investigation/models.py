"""Small domain-neutral response models used by the HTTP shell."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from ai_sre_investigation.domain import Alert, InvestigationBudget


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
