import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from ai_sre_investigation.config import Settings
from ai_sre_investigation.evaluation_report import load_evaluation_report
from ai_sre_investigation.main import create_app


def report() -> dict[str, object]:
    metrics = {
        "case_count": 32,
        "completion_rate": 1,
        "top1_accuracy": 0.75,
        "top3_accuracy": 1,
        "evidence_validity": 1,
        "unsupported_claim_rate": 0,
        "read_tool_success_rate": 1,
        "p50_duration_seconds": 0.1,
        "p95_duration_seconds": 0.2,
        "input_tokens": 100,
        "output_tokens": 50,
        "p50_cost_usd": 0.001,
        "p95_cost_usd": 0.002,
        "security_pass_rate": 1,
        "trace_completeness": 1,
    }
    return {
        "schema_version": 1,
        "dataset": "stage6-faults-v1",
        "dataset_sha256": "a" * 64,
        "mode": "replay",
        "commit": "b" * 40,
        "generated_at": "2026-09-04T00:00:00+00:00",
        "gate_profile": "evidence-first-v3",
        "passed": True,
        "gate_failures": [],
        "comparison": {
            "top1_accuracy_delta": 0.25,
            "top3_accuracy_delta": 0,
            "token_cost_proxy_change": 0.1,
        },
        "profiles": [
            {
                "prompt_version": "evidence-first-v3",
                "prompt_sha256": "c" * 64,
                "model_id": "replay-v1",
                "metrics": metrics,
                "family_metrics": {"latency": metrics},
                "gate_failures": [],
                "cases": [
                    {
                        "case_id": "case-pass",
                        "failure_categories": [],
                        "trace_id": "trace-pass",
                        "checkpoint_ref": "/secret/local/path",
                    },
                    {
                        "case_id": "case-fail",
                        "failure_categories": ["retrieval_failure"],
                        "trace_id": "trace-fail",
                        "tool_recording_ref": "secret://recording",
                    },
                ],
            }
        ],
    }


def test_loads_bounded_public_projection(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report()), encoding="utf-8")

    loaded = load_evaluation_report(str(path), 100_000)

    assert loaded.passed
    assert loaded.profiles[0].metrics.case_count == 32
    assert [item.case_id for item in loaded.profiles[0].failed_cases] == ["case-fail"]
    serialized = loaded.model_dump_json()
    assert "secret" not in serialized
    assert "checkpoint_ref" not in serialized


def test_rejects_oversized_or_invalid_report(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report()), encoding="utf-8")
    with pytest.raises(ValueError, match="size limit"):
        load_evaluation_report(str(path), 10)

    invalid = report()
    invalid["dataset_sha256"] = "invalid"
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_evaluation_report(str(path), 100_000)


def test_latest_evaluation_endpoint_handles_success_missing_and_invalid(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "report.json"
        path.write_text(json.dumps(report()), encoding="utf-8")
        app = create_app(Settings(environment="test", evaluation_report_path=str(path)))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/evaluations/latest")
            assert response.status_code == 200
            assert response.json()["dataset"] == "stage6-faults-v1"

            path.unlink()
            assert (await client.get("/api/v1/evaluations/latest")).status_code == 404

            path.write_text("{", encoding="utf-8")
            assert (await client.get("/api/v1/evaluations/latest")).status_code == 503

    asyncio.run(scenario())
