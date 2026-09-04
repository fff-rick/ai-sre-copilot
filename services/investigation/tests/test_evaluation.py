import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ai_sre_investigation.evaluation import (
    REDACTED,
    FrozenToolRecording,
    RecordedToolExchange,
    RecordingToolClient,
    ReplayToolClient,
    freeze_tool_recording,
    sanitize_recording_value,
)
from ai_sre_investigation.fakes import FakeModelClient, FakeToolClient
from ai_sre_investigation.ports import ArtifactReference, ToolRequest, ToolResponse
from ai_sre_investigation.stage6 import (
    QualityThresholds,
    aggregate_metrics,
    dataset_checksum,
    evaluate_stage6,
    expand_cases,
    load_dataset,
    quality_gate,
    render_markdown,
)
from ai_sre_investigation.tool_gateway_client import ToolGatewayError
from ai_sre_investigation.workflow import InvestigationWorkflow

NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[3]
DATASET_PATH = ROOT / "evals" / "stage6-cases.json"
REQUEST = ToolRequest(
    investigation_id="inv-recording",
    trace_id="0123456789abcdef",
    tool_name="prometheus.query",
    arguments={"promql": "up", "api_key": "must-not-survive"},
)


def test_recursive_recording_redaction_covers_keys_and_token_shapes() -> None:
    value = {
        "Authorization": "Bearer top-secret-token",
        "nested": [
            {"password": "correct horse battery staple"},
            "header Bearer abc.def.ghi",
            "AKIAABCDEFGHIJKLMNOP",
            "sk-abcdefghijklmnop1234",
        ],
        "safe": "visible",
    }

    sanitized = sanitize_recording_value(value)

    assert sanitized["Authorization"] == REDACTED
    assert sanitized["nested"] == [
        {"password": REDACTED},
        f"header {REDACTED}",
        REDACTED,
        REDACTED,
    ]
    assert sanitized["safe"] == "visible"


def test_record_freeze_and_exact_replay_are_redacted_and_checksum_verified() -> None:
    inner = FakeToolClient(
        {
            "prometheus.query": {
                "value": 1,
                "access_token": "secret-token",
                "message": "Bearer abc.def.ghi",
            }
        }
    )
    recorder = RecordingToolClient(
        inner, dataset_id="dataset-v1", case_id="CASE-1", now=lambda: NOW
    )

    response = asyncio.run(recorder.execute_read(REQUEST))
    frozen = recorder.freeze("recording-case-1", frozen_at=NOW)
    replay = ReplayToolClient(frozen)
    replayed = asyncio.run(replay.execute_read(REQUEST))

    assert response.data == replayed.data
    assert replayed.data == {
        "value": 1,
        "access_token": REDACTED,
        "message": REDACTED,
    }
    assert replayed.redacted is True
    assert frozen.reference.startswith("recording://dataset-v1/CASE-1/")
    assert frozen.exchanges[0].arguments["api_key"] == REDACTED

    tampered = frozen.model_dump(mode="json")
    tampered["exchanges"][0]["response_data"]["value"] = 2
    with pytest.raises(ValidationError, match="checksum mismatch"):
        FrozenToolRecording.model_validate(tampered)


def test_recorded_failures_replay_with_stable_semantics_and_misses_are_rejected() -> None:
    class FailingClient:
        async def execute_read(self, request: ToolRequest) -> ToolResponse:
            del request
            raise ToolGatewayError("SOURCE_UNAVAILABLE", "backend unavailable", True)

    recorder = RecordingToolClient(
        FailingClient(), dataset_id="dataset-v1", case_id="CASE-2", now=lambda: NOW
    )
    with pytest.raises(ToolGatewayError):
        asyncio.run(recorder.execute_read(REQUEST))
    frozen = recorder.freeze("recording-case-2", frozen_at=NOW)
    replay = ReplayToolClient(frozen)
    with pytest.raises(ToolGatewayError) as replayed:
        asyncio.run(replay.execute_read(REQUEST))
    assert replayed.value.code == "SOURCE_UNAVAILABLE"
    assert replayed.value.retryable is True

    unrecorded = REQUEST.model_copy(update={"arguments": {"promql": "other"}})
    with pytest.raises(ToolGatewayError) as missing:
        asyncio.run(ReplayToolClient(frozen).execute_read(unrecorded))
    assert missing.value.code == "REPLAY_MISS"


def test_recording_preserves_bounded_artifact_metadata_after_uri_redaction() -> None:
    class ArtifactClient:
        async def execute_read(self, request: ToolRequest) -> ToolResponse:
            return ToolResponse(
                tool_name=request.tool_name,
                data=None,
                source_ref="gateway://artifact",
                artifact=ArtifactReference(
                    uri="artifact://logs/bundle?token=secret-value",
                    sha256="a" * 64,
                    size_bytes=42,
                ),
            )

    recorder = RecordingToolClient(
        ArtifactClient(), dataset_id="dataset-v1", case_id="CASE-A", now=lambda: NOW
    )
    response = asyncio.run(recorder.execute_read(REQUEST))
    frozen = recorder.freeze("recording-case-a", frozen_at=NOW)
    replayed = asyncio.run(ReplayToolClient(frozen).execute_read(REQUEST))

    assert response.redacted is True
    assert replayed.artifact is not None
    assert replayed.artifact.uri == f"artifact://logs/bundle{REDACTED}"
    assert replayed.artifact.sha256 == "a" * 64


def test_generic_recording_failure_does_not_leak_exception_text() -> None:
    class BrokenClient:
        async def execute_read(self, request: ToolRequest) -> ToolResponse:
            del request
            raise RuntimeError("private backend detail")

    recorder = RecordingToolClient(
        BrokenClient(), dataset_id="dataset-v1", case_id="CASE-3", now=lambda: NOW
    )
    with pytest.raises(RuntimeError):
        asyncio.run(recorder.execute_read(REQUEST))
    assert recorder.exchanges[0].error_code == "RUNTIMEERROR"
    assert recorder.exchanges[0].error_message == "tool call failed during recording"


def test_recording_requires_a_valid_outcome_and_at_least_one_exchange() -> None:
    with pytest.raises(ValidationError, match="source_ref"):
        RecordedToolExchange(tool_name="prometheus.query", arguments={})
    with pytest.raises(ValidationError, match="error_message"):
        RecordedToolExchange(tool_name="prometheus.query", arguments={}, error_code="FAILED")
    with pytest.raises(ValidationError):
        freeze_tool_recording(
            recording_id="empty-recording",
            dataset_id="dataset-v1",
            case_id="CASE-4",
            exchanges=[],
            captured_at=NOW,
            frozen_at=NOW,
        )


def test_frozen_dataset_expands_to_32_diverse_replay_cases() -> None:
    dataset = load_dataset(DATASET_PATH)
    cases = expand_cases(dataset)

    assert dataset.expected_case_count == 32
    assert len(cases) == 32
    assert len({item.case_id for item in cases}) == 32
    assert len({item.family for item in cases}) == 8
    assert len({item.service for item in cases}) > 8
    assert sum(item.prompt_injection for item in cases) == 8
    assert {item.environment for item in cases} == {
        "staging",
        "test",
        "security-test",
    }

    raw = json.loads(DATASET_PATH.read_text())
    assert raw["content_sha256"] == dataset_checksum(raw)
    raw["expected_case_count"] = 31
    with pytest.raises(ValidationError):
        type(dataset).model_validate(raw)


def test_stage6_replay_compares_prompts_and_passes_all_gates(tmp_path: Path) -> None:
    report = asyncio.run(
        evaluate_stage6(
            dataset=load_dataset(DATASET_PATH),
            output_root=tmp_path,
            mode="replay",
        )
    )

    assert report["passed"] is True
    assert report["gate_failures"] == []
    baseline, candidate = report["profiles"]
    assert baseline["metrics"]["case_count"] == 32
    assert baseline["metrics"]["top1_accuracy"] == 0.75
    assert candidate["metrics"]["top1_accuracy"] == 1.0
    assert candidate["metrics"]["top3_accuracy"] == 1.0
    assert candidate["metrics"]["security_pass_rate"] == 1.0
    assert candidate["metrics"]["trace_completeness"] == 1.0
    assert candidate["metrics"]["p95_cost_usd"] > 0
    assert report["pricing"]["kind"] == "reference-only"
    assert report["comparison"]["top1_accuracy_delta"] == 0.25
    checkpoint = Path(candidate["cases"][0]["checkpoint_ref"])
    assert checkpoint.is_file()
    assert candidate["cases"][0]["tool_recording_ref"].startswith("recording://")
    rendered = render_markdown(report)
    assert "Decision: PASS" in rendered
    assert "reasoning_failure" in rendered


def test_quality_gate_reports_every_threshold_failure_and_markdown_diagnostics() -> None:
    case: dict[str, Any] = {
        "completed": False,
        "top1_hit": False,
        "top3_hit": False,
        "evidence_valid": False,
        "citation_count": 2,
        "valid_citation_count": 0,
        "unsupported_claims": 2,
        "claim_count": 2,
        "tool_calls": 4,
        "tool_successes": 0,
        "duration_seconds": 181.0,
        "input_tokens": 1,
        "output_tokens": 1,
        "estimated_cost_usd": 0.1,
        "security_case": True,
        "security_passed": False,
        "trace_complete": False,
    }
    metrics = aggregate_metrics([case])
    failures = quality_gate(metrics, QualityThresholds())

    assert len(failures) == 10
    assert any("security_pass_rate" in value for value in failures)
    assert any("unsupported_claim_rate" in value for value in failures)
    assert any("p95_duration_seconds" in value for value in failures)

    report = {
        "passed": False,
        "commit": "abc",
        "dataset": "dataset",
        "dataset_sha256": "0" * 64,
        "mode": "replay",
        "gate_profile": "candidate",
        "gate_failures": failures,
        "profiles": [
            {
                "prompt_version": "candidate",
                "metrics": metrics,
                "cases": [
                    {
                        **case,
                        "case_id": "CASE-FAIL",
                        "failure_categories": ["permission_failure"],
                        "trace_id": "trace",
                        "checkpoint_ref": "checkpoint",
                        "tool_recording_ref": "recording",
                    }
                ],
            }
        ],
    }
    rendered = render_markdown(report)
    assert "Decision: FAIL" in rendered
    assert "permission_failure" in rendered
    assert "Gate failures" in rendered


def test_workflow_rejects_unknown_prompt_profile() -> None:
    with pytest.raises(ValueError, match="unknown prompt version"):
        InvestigationWorkflow(
            model=FakeModelClient({"hypotheses": []}),
            tools=FakeToolClient({}),
            prompt_version="unknown",
        )


def test_stage6_rejects_negative_cost_rates(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cost rates"):
        asyncio.run(
            evaluate_stage6(
                dataset=load_dataset(DATASET_PATH),
                output_root=tmp_path,
                mode="replay",
                input_cost_usd_per_million=-1,
            )
        )
