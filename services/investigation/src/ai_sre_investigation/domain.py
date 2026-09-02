"""Validated domain objects for an evidence-first investigation."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class FrozenModel(BaseModel):
    """Immutable-by-contract base for checkpoint and API domain objects."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class InvestigationStatus(StrEnum):
    RECEIVED = "RECEIVED"
    SCOPING = "SCOPING"
    COLLECTING = "COLLECTING"
    HYPOTHESIZING = "HYPOTHESIZING"
    VERIFYING = "VERIFYING"
    RECOMMENDING = "RECOMMENDING"
    REPORTING = "REPORTING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class Severity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class TimeWindow(FrozenModel):
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def ordered(self) -> TimeWindow:
        if self.start >= self.end:
            raise ValueError("time window start must be before end")
        if (self.end - self.start).total_seconds() > 86_400:
            raise ValueError("time window cannot exceed 24 hours")
        return self


class Alert(FrozenModel):
    alert_id: str = Field(min_length=1, max_length=255)
    service: str = Field(pattern=r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
    severity: Severity
    summary: str = Field(min_length=1, max_length=2_000)
    time_window: TimeWindow
    source_ref: str = Field(min_length=1, max_length=2_000)
    labels: dict[str, str] = Field(default_factory=dict, max_length=50)

    @field_validator("labels")
    @classmethod
    def bounded_labels(cls, value: dict[str, str]) -> dict[str, str]:
        if any(len(key) > 100 or len(item) > 500 for key, item in value.items()):
            raise ValueError("alert label key or value exceeds its limit")
        return value


class InvestigationBudget(FrozenModel):
    max_model_calls: int = Field(default=3, ge=1, le=20)
    max_tool_calls: int = Field(default=6, ge=1, le=50)
    max_total_seconds: int = Field(default=180, ge=1, le=3_600)
    max_input_tokens: int = Field(default=30_000, ge=1, le=2_000_000)
    max_output_tokens: int = Field(default=8_000, ge=1, le=200_000)
    max_verification_rounds: int = Field(default=2, ge=1, le=5)


class BudgetUsage(FrozenModel):
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class Investigation(FrozenModel):
    investigation_id: str = Field(min_length=1, max_length=255)
    trace_id: str = Field(pattern=r"^[a-f0-9]{16,32}$")
    alert: Alert
    status: InvestigationStatus = InvestigationStatus.RECEIVED
    budget: InvestigationBudget = Field(default_factory=InvestigationBudget)
    model_profile: str = Field(default="default", min_length=1, max_length=100)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class EvidenceReliability(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Evidence(FrozenModel):
    evidence_id: str = Field(pattern=r"^ev-[a-f0-9]{16}$")
    source_type: str = Field(min_length=1, max_length=100)
    source_ref: str = Field(min_length=1, max_length=2_000)
    query: dict[str, Any]
    observed_at: AwareDatetime
    content_excerpt: str = Field(max_length=4_000)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    structured_facts: dict[str, Any] | list[Any] | None
    reliability: EvidenceReliability


class EvidenceGap(FrozenModel):
    source_type: str = Field(min_length=1, max_length=100)
    error_code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INCONCLUSIVE = "inconclusive"


class Hypothesis(FrozenModel):
    hypothesis_id: str = Field(pattern=r"^hyp-[a-zA-Z0-9_-]{1,60}$")
    statement: str = Field(min_length=1, max_length=1_000)
    rank: int = Field(ge=1, le=3)
    confidence: float = Field(ge=0, le=1)
    supporting_evidence_ids: list[str] = Field(min_length=1, max_length=20)
    contradicting_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    next_checks: list[str] = Field(default_factory=list, max_length=10)


class HypothesisProposal(FrozenModel):
    """Model-owned semantic fields; identifiers and verification remain code-owned."""

    statement: str = Field(min_length=1, max_length=1_000)
    rank: int = Field(ge=1, le=3)
    confidence: float = Field(ge=0, le=1)
    supporting_evidence_ids: list[str] = Field(min_length=1, max_length=20)
    contradicting_evidence_ids: list[str] = Field(max_length=20)
    next_checks: list[str] = Field(max_length=10)


class HypothesisProposalBatch(FrozenModel):
    hypotheses: list[HypothesisProposal] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_and_ordered(self) -> HypothesisProposalBatch:
        ranks = [item.rank for item in self.hypotheses]
        if len(ranks) != len(set(ranks)) or sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise ValueError("hypothesis ranks must be unique and contiguous from one")
        return self


class HypothesisBatch(FrozenModel):
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_and_ordered(self) -> HypothesisBatch:
        ranks = [item.rank for item in self.hypotheses]
        identifiers = [item.hypothesis_id for item in self.hypotheses]
        if len(ranks) != len(set(ranks)) or sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise ValueError("hypothesis ranks must be unique and contiguous from one")
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("hypothesis identifiers must be unique")
        return self


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ProposedAction(FrozenModel):
    action_id: str = Field(pattern=r"^act-[a-zA-Z0-9_-]{1,60}$")
    description: str = Field(min_length=1, max_length=1_000)
    target: str = Field(min_length=1, max_length=253)
    risk_level: RiskLevel
    expected_effect: str = Field(min_length=1, max_length=1_000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    requires_approval: bool = True


class InvestigationReport(FrozenModel):
    investigation_id: str
    status: InvestigationStatus
    impact_summary: str
    hypotheses: list[Hypothesis]
    evidence: list[Evidence]
    evidence_gaps: list[EvidenceGap]
    proposed_actions: list[ProposedAction]
    uncertainty: list[str]
    budget_usage: BudgetUsage
    completed_at: AwareDatetime
