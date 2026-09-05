"""Validated, bounded projection of the latest evaluation report for the Web UI."""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvaluationMetrics(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    case_count: int = Field(ge=0)
    completion_rate: float = Field(ge=0, le=1)
    top1_accuracy: float = Field(ge=0, le=1)
    top3_accuracy: float = Field(ge=0, le=1)
    evidence_validity: float = Field(ge=0, le=1)
    unsupported_claim_rate: float = Field(ge=0, le=1)
    read_tool_success_rate: float = Field(ge=0, le=1)
    p50_duration_seconds: float = Field(ge=0)
    p95_duration_seconds: float = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    p50_cost_usd: float = Field(ge=0)
    p95_cost_usd: float = Field(ge=0)
    security_pass_rate: float = Field(ge=0, le=1)
    trace_completeness: float = Field(ge=0, le=1)


class EvaluationCaseFailure(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    case_id: str = Field(min_length=1, max_length=128)
    failure_categories: list[str] = Field(max_length=16)
    trace_id: str = Field(min_length=1, max_length=128)


class _EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    case_id: str
    failure_categories: list[str] = Field(default_factory=list)
    trace_id: str


class EvaluationProfile(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    prompt_version: str = Field(min_length=1, max_length=128)
    prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_id: str = Field(min_length=1, max_length=256)
    metrics: EvaluationMetrics
    family_metrics: dict[str, EvaluationMetrics]
    gate_failures: list[str] = Field(max_length=64)
    failed_cases: list[EvaluationCaseFailure]


class _EvaluationProfileSource(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    prompt_version: str
    prompt_sha256: str
    model_id: str
    metrics: EvaluationMetrics
    family_metrics: dict[str, EvaluationMetrics]
    gate_failures: list[str]
    cases: list[_EvaluationCase]


class EvaluationComparison(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    top1_accuracy_delta: float
    top3_accuracy_delta: float
    token_cost_proxy_change: float


class _EvaluationReportSource(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: int = Field(ge=1)
    dataset: str = Field(min_length=1, max_length=128)
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mode: str = Field(min_length=1, max_length=32)
    commit: str = Field(pattern=r"^[a-f0-9]{7,64}$")
    generated_at: str = Field(min_length=1, max_length=64)
    gate_profile: str = Field(min_length=1, max_length=128)
    passed: bool
    gate_failures: list[str] = Field(max_length=64)
    comparison: EvaluationComparison
    profiles: list[_EvaluationProfileSource] = Field(min_length=1, max_length=8)


class EvaluationReport(BaseModel):
    """Public report intentionally excludes prompts, checkpoints, paths, and recordings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    dataset: str
    dataset_sha256: str
    mode: str
    commit: str
    generated_at: str
    gate_profile: str
    passed: bool
    gate_failures: list[str]
    comparison: EvaluationComparison
    profiles: list[EvaluationProfile]


def load_evaluation_report(path_value: str, max_bytes: int) -> EvaluationReport:
    """Load one startup-controlled report without exposing arbitrary raw artifact content."""

    path = Path(path_value)
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError("evaluation report exceeds configured size limit")
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    source = _EvaluationReportSource.model_validate(raw)
    profiles = [
        EvaluationProfile(
            prompt_version=profile.prompt_version,
            prompt_sha256=profile.prompt_sha256,
            model_id=profile.model_id,
            metrics=profile.metrics,
            family_metrics=profile.family_metrics,
            gate_failures=profile.gate_failures,
            failed_cases=[
                EvaluationCaseFailure.model_validate(item.model_dump())
                for item in profile.cases
                if item.failure_categories
            ],
        )
        for profile in source.profiles
    ]
    return EvaluationReport(
        schema_version=source.schema_version,
        dataset=source.dataset,
        dataset_sha256=source.dataset_sha256,
        mode=source.mode,
        commit=source.commit,
        generated_at=source.generated_at,
        gate_profile=source.gate_profile,
        passed=source.passed,
        gate_failures=source.gate_failures,
        comparison=source.comparison,
        profiles=profiles,
    )
