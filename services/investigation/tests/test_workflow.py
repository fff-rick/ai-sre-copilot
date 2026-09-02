import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from ai_sre_investigation.domain import (
    Alert,
    Investigation,
    InvestigationBudget,
    Severity,
    TimeWindow,
)
from ai_sre_investigation.fakes import (
    FailingFakeToolClient,
    FakeModelClient,
    FakeToolClient,
)
from ai_sre_investigation.model_client import ModelProviderError
from ai_sre_investigation.ports import ModelRequest, ModelResponse
from ai_sre_investigation.tool_gateway_client import ToolGatewayError
from ai_sre_investigation.workflow import InvestigationState, InvestigationWorkflow

NOW = datetime(2026, 9, 2, 10, tzinfo=UTC)


def investigation(*, budget: InvestigationBudget | None = None) -> Investigation:
    return Investigation(
        investigation_id="inv-stage3",
        trace_id="0123456789abcdef",
        alert=Alert(
            alert_id="alert-payment-errors",
            service="payment",
            severity=Severity.CRITICAL,
            summary="Payment error rate is above five percent.",
            time_window=TimeWindow(start=NOW - timedelta(minutes=15), end=NOW),
            source_ref="alertmanager://payment-errors",
        ),
        budget=budget or InvestigationBudget(),
        created_at=NOW,
        updated_at=NOW,
    )


TOOL_RESPONSES: dict[str, dict[str, object]] = {
    "prometheus.query": {"error_rate": 0.17},
    "loki.query_range": {"messages": ["connection refused to inventory"]},
    "tempo.search_traces": {"slow_dependency": "inventory"},
    "releases.list": {"release": "payment-v42"},
}


def evidence_id(tool_name: str, data: object) -> str:
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    seed = f"{tool_name}:fake://{tool_name}:{digest}"
    return f"ev-{hashlib.sha256(seed.encode()).hexdigest()[:16]}"


def valid_hypotheses(confidence: float = 0.91) -> dict[str, Any]:
    metric = evidence_id("prometheus.query", TOOL_RESPONSES["prometheus.query"])
    logs = evidence_id("loki.query_range", TOOL_RESPONSES["loki.query_range"])
    traces = evidence_id("tempo.search_traces", TOOL_RESPONSES["tempo.search_traces"])
    return {
        "hypotheses": [
            {
                "statement": "Inventory dependency is unavailable.",
                "rank": 1,
                "confidence": confidence,
                "supporting_evidence_ids": [logs, traces],
                "contradicting_evidence_ids": [],
                "next_checks": ["Review inventory health."],
            },
            {
                "statement": "The latest payment release introduced a regression.",
                "rank": 2,
                "confidence": 0.5,
                "supporting_evidence_ids": [metric],
                "contradicting_evidence_ids": [],
                "next_checks": [],
            },
            {
                "statement": "Payment capacity is insufficient.",
                "rank": 3,
                "confidence": 0.2,
                "supporting_evidence_ids": [metric],
                "contradicting_evidence_ids": [],
                "next_checks": [],
            },
        ]
    }


def run(workflow: InvestigationWorkflow, item: Investigation | None = None) -> InvestigationState:
    return asyncio.run(workflow.run(item or investigation()))


def test_fake_model_completes_full_evidence_backed_top_three_flow() -> None:
    model = FakeModelClient(valid_hypotheses())
    tools = FakeToolClient(TOOL_RESPONSES)

    state = run(InvestigationWorkflow(model=model, tools=tools, now=lambda: NOW))

    assert state["status"] == "COMPLETED"
    assert len(state["evidence"]) == 4
    assert len(state["hypotheses"]) == 3
    assert [item["rank"] for item in state["hypotheses"]] == [1, 2, 3]
    assert all(item["verification_status"] == "supported" for item in state["hypotheses"])
    assert state["report"] is not None
    assert state["report"]["status"] == "COMPLETED"
    assert len(model.requests) == 1
    assert len(tools.requests) == 4


def test_one_unavailable_source_becomes_an_explicit_gap() -> None:
    model_payload = valid_hypotheses()
    model_payload["hypotheses"][0]["supporting_evidence_ids"] = [
        evidence_id("tempo.search_traces", TOOL_RESPONSES["tempo.search_traces"])
    ]
    tools = FailingFakeToolClient(
        TOOL_RESPONSES,
        {"loki.query_range": ToolGatewayError("SOURCE_UNAVAILABLE", "Loki unavailable", True)},
    )

    state = run(
        InvestigationWorkflow(model=FakeModelClient(model_payload), tools=tools, now=lambda: NOW)
    )

    assert state["status"] == "COMPLETED"
    assert len(state["evidence"]) == 3
    assert state["evidence_gaps"] == [
        {
            "source_type": "loki.query_range",
            "error_code": "SOURCE_UNAVAILABLE",
            "message": "Loki unavailable",
            "retryable": True,
        }
    ]
    assert state["report"] is not None
    assert "Missing loki.query_range" in state["report"]["uncertainty"][0]


def test_invalid_evidence_reference_is_repaired_exactly_once() -> None:
    invalid = valid_hypotheses()
    invalid["hypotheses"][0]["supporting_evidence_ids"] = ["ev-0000000000000000"]
    model = FakeModelClient([invalid, valid_hypotheses()])

    state = run(
        InvestigationWorkflow(model=model, tools=FakeToolClient(TOOL_RESPONSES), now=lambda: NOW)
    )

    assert state["status"] == "COMPLETED"
    assert state["repair_attempted"] is True
    assert len(model.requests) == 2
    assert "failed validation" in model.requests[1].input_text


def test_budget_stops_new_calls_and_still_reports() -> None:
    budget = InvestigationBudget(max_tool_calls=1, max_model_calls=1)
    payload = valid_hypotheses()
    payload["hypotheses"] = [payload["hypotheses"][1]]
    payload["hypotheses"][0]["rank"] = 1

    state = run(
        InvestigationWorkflow(
            model=FakeModelClient(payload),
            tools=FakeToolClient(TOOL_RESPONSES),
            now=lambda: NOW,
        ),
        investigation(budget=budget),
    )

    assert state["status"] == "COMPLETED"
    assert state["budget_exhausted"] is True
    assert state["usage"] == {
        "model_calls": 1,
        "tool_calls": 1,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    assert state["report"] is not None
    assert "budget" in state["report"]["uncertainty"][-1].lower()


def test_cancellation_is_checked_before_external_calls() -> None:
    async def cancelled(_: str) -> bool:
        return True

    model = FakeModelClient(valid_hypotheses())
    tools = FakeToolClient(TOOL_RESPONSES)
    state = run(
        InvestigationWorkflow(model=model, tools=tools, cancel_check=cancelled, now=lambda: NOW)
    )

    assert state["status"] == "CANCELLED"
    assert not model.requests
    assert not tools.requests


def test_resume_uses_checkpoint_without_repeating_completed_tools() -> None:
    saver = InMemorySaver()
    model = FakeModelClient(valid_hypotheses())
    tools = FakeToolClient(TOOL_RESPONSES)
    interrupted = InvestigationWorkflow(
        model=model,
        tools=tools,
        checkpointer=saver,
        now=lambda: NOW,
        interrupt_after=["verify"],
    )
    state = run(interrupted)
    assert state["status"] == "VERIFYING"
    assert len(tools.requests) == 4

    restarted = InvestigationWorkflow(model=model, tools=tools, checkpointer=saver, now=lambda: NOW)
    resumed = asyncio.run(restarted.resume("inv-stage3"))

    assert resumed["status"] == "COMPLETED"
    assert len(tools.requests) == 4


def test_retryable_model_failure_is_bounded_and_reports_existing_evidence() -> None:
    class UnavailableModel:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, request: ModelRequest) -> ModelResponse:
            del request
            self.calls += 1
            raise ModelProviderError("HTTP_429", "model provider rejected the request", True)

    model = UnavailableModel()
    state = run(
        InvestigationWorkflow(
            model=model,
            tools=FakeToolClient(TOOL_RESPONSES),
            now=lambda: NOW,
            model_retry_base_seconds=0,
        )
    )

    assert state["status"] == "COMPLETED"
    assert model.calls == 2
    assert len(state["evidence"]) == 4
    assert state["evidence_gaps"][-1]["source_type"] == "model"
    assert state["report"] is not None
    assert "No valid evidence-backed hypothesis" in state["report"]["uncertainty"][-1]
